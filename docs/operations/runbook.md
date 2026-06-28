# 安否確認システム 運用ランブック

- **対象読者**: オペレーター（Cognito `Administrator` グループ所属）、SRE、開発者
- **関連要件**: Req 4 / Req 14.5 / Req 14.6 / NFR2
- **関連設計**: design.md「想定リスクと対策」/ Phase 12 観測・監視レイヤ
- **想定環境**: AWS リージョン ap-northeast-1、AWS CLI Profile `AWS-security-check`、PowerShell では `$env:PYTHONUTF8="1"` を設定すること

> インシデント対応（DLQ 滞留・録音欠落・Transcript 欠落・SLA 違反・辞書誤更新等）の詳細手順は [`incident-response.md`](./incident-response.md) を、CloudWatch アラームの一覧と推奨対応は [`monitoring.md`](./monitoring.md) を参照すること。

---

## 1. システム構成サマリ

| 層                   | 主要リソース                                                                                                              | 役割                                 |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| オーケストレーション | `safety-confirmation-cycle-<env>` (Step Functions)                                                                        | サイクル全体の制御                   |
| 発信                 | Amazon Connect (Outbound Contact Flow) + `ConnectDispatcherFn`                                                            | 自動発信                             |
| 音声処理             | `RecordingMetadataWriterFn` + `TranscribeStarterFn` + `KeywordMatcherFn`                                                  | 録音メタ書込 / 音声認識 / 安否判定   |
| データ               | `Cycle-<env>` / `Response-<env>` / `Employee-<env>` / `KeywordDictionary-<env>` (DynamoDB), S3 録音 / Transcript バケット | サイクル / 応答 / 社員 / 辞書 / 録音 |
| 通知                 | `OperatorTopic` (SNS、email Subscription)                                                                                 | アラーム・SLA 違反通知               |
| 監視                 | 6 件の CloudWatch Alarm + 1 件の Metric Filter                                                                            | 異常検知                             |
| DLQ                  | `safety-confirmation-recording-meta-dlq-<env>` (SQS)                                                                      | RecordingMetadataWriter 失敗保持     |

---

## 2. サイクル起動手順 (Req 4.1)

### 2.1 SPA UI 経由（推奨）

1. `https://<spa-domain>/` にアクセスし、`Administrator` グループのユーザーで Cognito ログイン。
2. 「サイクル管理」画面で対象者リストを確認し、「サイクル起動」ボタンを押下。
3. SPA は `POST /cycles` を `Idempotency-Key` ヘッダ付きで送信し、`202 Accepted` を確認。
4. ダッシュボードに新規 `Cycle ID` と Step Functions 実行 ARN が表示されたら起動成功。

### 2.2 API 直叩き（緊急時のみ）

```powershell
$env:PYTHONUTF8 = "1"
$idem = [guid]::NewGuid().Guid
$token = "<Cognito ID Token>"  # SPA から DevTools で取得、または aws cognito-idp initiate-auth で取得

curl.exe -X POST "https://<api-domain>/cycles" `
  -H "Authorization: Bearer $token" `
  -H "Idempotency-Key: $idem" `
  -H "Content-Type: application/json" `
  --data "{}"
```

レスポンス例:

```json
{
  "cycleId": "f3d8...c2",
  "status": "RUNNING",
  "startedAt": "2026-06-28T10:00:00Z",
  "dictionaryVersion": 17
}
```

### 2.3 起動失敗時の確認ポイント（5 分以内のトリアージ）

| 症状                               | 確認先                                                                                   | 対処                                           |
| ---------------------------------- | ---------------------------------------------------------------------------------------- | ---------------------------------------------- |
| HTTP 400 `dictionary_empty`        | DynamoDB `KeywordDictionary-<env>` のメタレコード `version`                              | 辞書を 1 件以上登録してから再起動              |
| HTTP 409 `cycle_running`           | DynamoDB `Cycle-<env>` GSI `StatusStartedAtIndex` で `RUNNING` を検索                    | 当該サイクルの完了またはタイムアウト確定を待つ |
| HTTP 401 / 403                     | Cognito Token の `cognito:groups` に `Administrator` が含まれるか                        | グループ所属を再確認                           |
| HTTP 500                           | CloudWatch Logs `/aws/lambda/safety-confirmation-cycle-api-<env>`                        | 直近 5 分の ERROR ログから根本原因を特定       |
| Step Functions 実行 ARN が返らない | CloudWatch Logs `/aws/lambda/safety-confirmation-cycle-api-<env>` の `StartExecution` 行 | IAM Role 権限 / SFN ARN 設定値を確認           |

---

## 3. サイクル中断手順

### 3.1 Step Functions 実行を停止する

> CycleApi 側に中断 API は存在しない。中断は Step Functions を直接停止する。

```powershell
$env:PYTHONUTF8 = "1"
$cycleId = "<cycle id>"
$execArn = aws stepfunctions list-executions `
  --profile AWS-security-check `
  --state-machine-arn "arn:aws:states:ap-northeast-1:<account>:stateMachine:safety-confirmation-cycle-<env>" `
  --status-filter RUNNING `
  --query "executions[?starts_with(name, '$cycleId')].executionArn | [0]" `
  --output text

aws stepfunctions stop-execution `
  --profile AWS-security-check `
  --execution-arn $execArn `
  --cause "manual-stop-by-operator" `
  --error "ManualStop"
```

### 3.2 サイクルレコードを手動でタイムアウト確定する（要承認）

Step Functions 停止のみでは DynamoDB 上の `Cycle.status` は更新されない。フロントへの表示反映のため、以下を実施する。

> ⚠️ DynamoDB 直接更新は不可逆。**SRE 1 名以上の承認 + 変更チケット起票** を必須とする。

```powershell
aws dynamodb update-item `
  --profile AWS-security-check `
  --table-name "Cycle-<env>" `
  --key '{"cycleId":{"S":"<cycle id>"}}' `
  --update-expression "SET #s = :s, completedAt = :t" `
  --condition-expression "#s = :running" `
  --expression-attribute-names '{"#s":"status"}' `
  --expression-attribute-values '{":s":{"S":"TIMEOUT"},":t":{"S":"<ISO8601 UTC>"},":running":{"S":"RUNNING"}}'
```

未確定 Response の `UNREACHABLE` 強制更新は CycleFinalizer が `TIMER_60MIN` イベントで実施する設計なので、手動 update する必要はない。

---

## 4. 60 分タイムアウト時の対処 (Req 14.5)

### 4.1 基本フロー（自動）

1. 起動時刻 + 60 分に EventBridge ルール `cycle-60min-<cycleId>` が発火し、`CycleFinalizerFn` が起動する。
2. `CycleFinalizerFn` は `Cycle.status = TIMEOUT` を書き、未確定 Response を `voiceStatus = UNREACHABLE` で強制確定する。
3. 同 Lambda が `PutMetricData(Namespace=SafetyConfirmation, MetricName=CycleTimeout, Value=1)` を発行。
4. `CycleTimeoutAlarm` が `OperatorTopic` 経由でオペレーターにメール通知。

### 4.2 通知受信後のオペレーター作業（30 分以内）

1. SPA「サイクル詳細」画面で `status=TIMEOUT` を確認し、`UNREACHABLE` 確定者のリストをエクスポート。
2. **対面 / 別チャネル（社内チャット、メール）で再確認** を実施。
3. インバウンド受付期間（完了から 30 日以内）に折り返し電話が入った場合、`InboundHandler` 経由で Response が更新されることを期待する。
4. 30 日経過後は Response が確定値のまま固定される。事後分析が必要なら CloudWatch Logs Insights で確定タイムスタンプを抽出する。

### 4.3 EventBridge ルールが発火しない場合（異常系）

| 症状                               | 確認コマンド                                                            | 対処                                                                               |
| ---------------------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `cycle-60min-<cycleId>` ルール不在 | `aws events list-rules --name-prefix cycle-60min-`                      | `LoadTargets` 直後の `PutRule` 失敗。Logs を確認し手動で `CycleFinalizerFn` を呼出 |
| ルール存在するが Lambda 未起動     | EventBridge コンソール「メトリクス」                                    | Lambda Resource Policy / IAM Role / SFN status を確認                              |
| `CycleFinalizerFn` 内でエラー      | CloudWatch Logs `/aws/lambda/safety-confirmation-cycle-finalizer-<env>` | エラーログから対処、必要なら手動で finalizer payload を作成して invoke             |

---

## 5. SNS 通知の対応手順

> `OperatorTopic` (`safety-confirmation-operator-<env>`) は 1 本に集約。受信メッセージの件名で内容を判別する。

### 5.1 30 分 SLA 遅延警告 (Req 14.6)

- **件名例**: `ALARM: safety-confirmation-sla-warning-30min-<env>`
- **意味**: サイクル起動から 30 分時点で初回発信完了率が 100% 未達。
- **対応**:
  1. SPA「サイクル詳細」画面で「発信完了数 / 対象者総数」を確認。
  2. 未発信者リストを抽出し、CloudWatch Logs `/aws/lambda/safety-confirmation-connect-dispatcher-<env>` で `LimitExceededException` の有無を確認。
  3. Connect 同時通話 10 の上限張り付きが続く場合は、待機キューが消化されるまで監視を継続。

### 5.2 60 分タイムアウト (Req 14.5)

- **件名例**: `ALARM: safety-confirmation-cycle-timeout-<env>`
- **意味**: 60 分以内にサイクルが完了しなかった。
- **対応**: 上記 4.2 に従う。

### 5.3 DLQ 通知

- **件名例**: `ALARM: safety-confirmation-recording-upload-failure-<env>`
- **対応**: [`incident-response.md` §1](./incident-response.md#1-dlq-メッセージ滞留-req-109) に従う。

### 5.4 Lambda 大量エラー

- **件名例**: `ALARM: safety-confirmation-lambda-errors-<env>`
- **対応**: [`incident-response.md` §6](./incident-response.md#6-lambda-大量エラー運用観測) に従う。

### 5.5 Transcribe 失敗

- **件名例**: `ALARM: safety-confirmation-transcribe-failed-<env>`
- **対応**: [`incident-response.md` §3](./incident-response.md#3-transcript-欠落--transcribe-失敗-req-66) に従う。

### 5.6 未登録番号インバウンド連発

- **件名例**: `ALARM: safety-confirmation-inbound-unauthorized-<env>`
- **対応**:
  1. CloudWatch Logs `/aws/safety-confirmation/audit-<env>` で `event=INBOUND_CONTACT_RECEIVED && flow=NOT_REGISTERED` を `filter-log-events` で抽出。
  2. 5 分間に 10 件超 = brute force 兆候。発信元番号の傾向を確認。
  3. 必要なら Connect 側で発信元クォータ制限を追加する（変更チケット起票）。

---

## 6. 日次運用チェックリスト（朝礼前 10 分）

| 項目                         | 確認方法                                                                                           | 異常時の遷移先                                                                      |
| ---------------------------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| OperatorTopic 受信メール     | メールクライアントの受信箱                                                                         | 件名に応じて 5.x へ                                                                 |
| アクティブアラーム           | `aws cloudwatch describe-alarms --state-value ALARM`                                               | [`monitoring.md`](./monitoring.md) のアラーム表                                     |
| DLQ メッセージ数             | `aws sqs get-queue-attributes --queue-url <dlq-url> --attribute-names ApproximateNumberOfMessages` | [`incident-response.md` §1](./incident-response.md#1-dlq-メッセージ滞留-req-109)    |
| 直近 24h で失敗した SFN 実行 | `aws stepfunctions list-executions --status-filter FAILED`                                         | 失敗 ARN 別に Logs を確認                                                           |
| KeywordDictionary バージョン | `KeywordDictionary-<env>` のメタレコード `version` が前日と一致                                    | [`incident-response.md` §5](./incident-response.md#5-辞書誤更新の戻し方-req-85--87) |

---

## 7. リリース / 変更時のオペレーション

### 7.1 CFn デプロイ

```powershell
$env:PYTHONUTF8 = "1"
aws cloudformation package `
  --profile AWS-security-check `
  --template-file infrastructure/template.yaml `
  --s3-bucket safety-confirmation-cfn-artifacts-<account>-ap-northeast-1 `
  --s3-prefix packaged/<phase>/<date> `
  --output-template-file infrastructure/packaged.yaml

aws cloudformation deploy `
  --profile AWS-security-check `
  --template-file infrastructure/packaged.yaml `
  --stack-name safety-confirmation-<env> `
  --capabilities CAPABILITY_NAMED_IAM `
  --s3-bucket safety-confirmation-cfn-artifacts-<account>-ap-northeast-1 `
  --s3-prefix packaged/<phase>/<date> `
  --parameter-overrides EnvironmentName=<env> OperatorEmail=<email>
```

### 7.2 SNS Subscription 更新（OperatorEmail 変更時）

1. `aws cloudformation update-stack` で `OperatorEmail` パラメータを更新。
2. AWS から到着する `Subscription Confirmation` メールの `ConfirmSubscription` リンクをクリック。
3. `aws sns publish --topic-arn <OperatorTopicArn> --message "test" --subject "test"` で受信確認。

### 7.3 デプロイ後検証

- `aws cloudwatch describe-alarms --alarm-name-prefix safety-confirmation` で 6 件の Alarm が `OK` であること
- `aws lambda list-functions --query "Functions[?starts_with(FunctionName,'safety-confirmation-')].FunctionName"` で 19 件の Lambda が揃っていること
- SPA がログイン可能・サイクル一覧が表示されること

### 7.4 別 Profile での運用（AWS_PROFILE override、Task 15.11）

`infrastructure/scripts/{deploy,validate}.{ps1,sh}` の AWS CLI Profile は既定 `AWS-security-check`。AssumeRole 構成 / 別アカウント検証 / 個人開発環境では、環境変数 `AWS_PROFILE` を設定すれば 4 スクリプトすべてがそれを使用する（未設定時は既定値にフォールバック）。

```powershell
# PowerShell：一時的に別 Profile で deploy.ps1 を実行
$env:AWS_PROFILE = 'my-assumerole-profile'
try {
    pwsh -NoProfile -File infrastructure/scripts/deploy.ps1 -EnvironmentName stg -DryRun
} finally {
    Remove-Item Env:\AWS_PROFILE -ErrorAction SilentlyContinue
}
```

```bash
# bash：1 コマンドだけ別 Profile で実行
AWS_PROFILE=my-assumerole-profile bash infrastructure/scripts/deploy.sh stg --dry-run

# 一連の運用コマンドを同一 Profile で揃える場合
export AWS_PROFILE=my-assumerole-profile
bash infrastructure/scripts/validate.sh
bash infrastructure/scripts/deploy.sh stg --no-execute-changeset
```

スクリプトはサマリー出力に `AwsProfile : <選択された Profile>` を表示するので、想定どおりの Profile で動いているかを起動時に目視確認できる。詳細は [`deploy.md` §1.3.1](./deploy.md#131-既定-profile-名の-override-task-1511) を参照。

---

## 8. エスカレーションパス

| レベル          | 役割             | 連絡手段        | トリガー                                                            |
| --------------- | ---------------- | --------------- | ------------------------------------------------------------------- |
| L1 オペレーター | 当番             | 社内チャット    | OperatorTopic 受信、ダッシュボード異常                              |
| L2 SRE          | オンコール SRE   | 電話 + チャット | L1 で 30 分以内に解消しない / Cycle ステータス操作が必要 / DLQ 復旧 |
| L3 開発者       | プロダクト責任者 | 電話            | 本番影響 60 分超 / コード修正が必要                                 |
| L4 経営層       | 安全管理本部長   | 電話            | SLA 違反 + 災害初動への支障                                         |

---

## 改訂履歴

| 日付       | 改訂内容                                       | 起票者 |
| ---------- | ---------------------------------------------- | ------ |
| 2026-06-28 | 初版作成（Task 15.4、Phase 12 監視構成に整合） | kiro   |
