# 安否確認システム インシデント対応手順

- **対象読者**: オペレーター、SRE、開発者
- **関連要件**: Req 6.6 / Req 8.5 / Req 8.7 / Req 10.9 / Req 14.5 / Req 14.6 / Req 15.5 / NFR2 / NFR3
- **関連設計**: design.md「想定リスクと対策」
- **前提**: 通常運用と SNS 通知の一次対応は [`runbook.md`](./runbook.md) を参照。本ドキュメントは「異常検知後の復旧手順」を扱う。

> CloudWatch アラームと一次対応の対応表は [`monitoring.md`](./monitoring.md) を参照。

---

## 1. DLQ メッセージ滞留 (Req 10.9)

### 1.1 検知

- アラーム: `safety-confirmation-recording-upload-failure-<env>`
- メトリクス: `AWS/SQS NumberOfMessagesSent` (Dim: `QueueName=safety-confirmation-recording-meta-dlq-<env>`)
- 設計判断（実装側を真とする）: TranscribeStarter には DLQ を設けず、3 連敗で `raise` → `AWS/Lambda Errors` メトリクス経由で `TranscribeFailedAlarm` が発火する構成。**DLQ が存在するのは `RecordingMetadataWriterFn` のみ**。

### 1.2 滞留メッセージの調査

```powershell
$env:PYTHONUTF8 = "1"
$dlqUrl = aws cloudformation describe-stacks `
  --profile AWS-security-check `
  --stack-name safety-confirmation-<env> `
  --query "Stacks[0].Outputs[?OutputKey=='RecordingMetadataWriterDLQUrl'].OutputValue" `
  --output text

aws sqs get-queue-attributes `
  --profile AWS-security-check `
  --queue-url $dlqUrl `
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible OldestMessageAge

# メッセージを Peek（VisibilityTimeout 30 秒で受信、Body を確認）
aws sqs receive-message `
  --profile AWS-security-check `
  --queue-url $dlqUrl `
  --max-number-of-messages 10 `
  --visibility-timeout 30 `
  --attribute-names All `
  --message-attribute-names All
```

### 1.3 メッセージから元イベントを復元

`RecordingMetadataWriterDLQ` には EventBridge `Object Created` イベントが転送される。Body の `detail.bucket.name` / `detail.object.key` から S3 オブジェクトキー（例: `recordings/<cycleId>/<employeeId>/<contactId>.wav`）を抽出し、対応する `Cycle ID` / `Employee ID` を特定する。

### 1.4 失敗原因の特定

CloudWatch Logs Insights で `/aws/lambda/safety-confirmation-recording-meta-writer-<env>` を検索:

```sql
fields @timestamp, @message
| filter @message like /ERROR/ or @message like /_DdbWriteExhaustedError/
| filter @message like /<contactId>/
| sort @timestamp desc
| limit 20
```

代表的な失敗原因と対処:

| 原因                                     | 対処                                                                          |
| ---------------------------------------- | ----------------------------------------------------------------------------- |
| DynamoDB ThrottlingException 連発        | 一時的な PAY_PER_REQUEST 適応遅延。手動再ドライブで解消する場合が多い。       |
| KMS AccessDenied                         | Lambda Role の `kms:GenerateDataKey` 権限を確認。CFn template から再 deploy。 |
| `RecordingMetaTable` テーブル名 mismatch | Lambda Env Var `RECORDING_META_TABLE_NAME` を確認。                           |
| 不正な S3 オブジェクトキー               | 該当メッセージは復旧不可。1.6 の手順でメッセージ削除 + Response を手動更新。  |

### 1.5 復旧（再ドライブ）

```powershell
# メッセージ本文を JSON ファイルに保存後、RecordingMetadataWriterFn を直接 invoke
$payload = Get-Content -Raw .\dlq-message.json
aws lambda invoke `
  --profile AWS-security-check `
  --function-name safety-confirmation-recording-meta-writer-<env> `
  --payload $payload `
  --cli-binary-format raw-in-base64-out `
  .\out.json

# 成功確認後、DLQ から削除
aws sqs delete-message `
  --profile AWS-security-check `
  --queue-url $dlqUrl `
  --receipt-handle "<ReceiptHandle from receive-message>"
```

### 1.6 復旧不可の場合

- 該当 Response を手動で `callResultCode=ERROR` + `voiceStatus=UNREACHABLE` に確定（[5.3 の手動 update パターン](#53-ロールバック実行)を流用）
- 関係者（SRE / プロダクト責任者）に共有し、事後分析チケットを起票

---

## 2. 録音欠落 (Req 10.9)

### 2.1 検知

- ダッシュボードまたは履歴画面で「録音再生」ボタン押下 → 404 / 410 / S3 NoSuchKey エラー
- `Recording_Metadata` テーブルに該当行が存在するが、S3 オブジェクトが見つからない

### 2.2 調査

```powershell
$cycleId = "<cycle id>"
$employeeId = "<employee id>"
$contactId = "<contact id>"

# Recording_Metadata から S3 オブジェクトキーを取得
aws dynamodb get-item `
  --profile AWS-security-check `
  --table-name "RecordingMetadata-<env>" `
  --key "{`"cycleId`":{`"S`":`"$cycleId`"},`"employeeIdSeq`":{`"S`":`"$employeeId#001`"}}"

# S3 オブジェクトの存在確認
aws s3api head-object `
  --profile AWS-security-check `
  --bucket "safety-confirmation-recordings-<env>-<account>-<region>" `
  --key "recordings/$cycleId/$employeeId/$contactId.wav"
```

### 2.3 原因切り分け

| 観察                                        | 想定原因                                 | 対処                                    |
| ------------------------------------------- | ---------------------------------------- | --------------------------------------- |
| S3 オブジェクト不在 + DDB 行あり            | RecordingRelocator 移動後に S3 が破損    | 復旧不可。2.4 へ                        |
| S3 オブジェクト不在 + DDB 行なし            | Connect 側で録音失敗（自動リトライ枯渇） | 復旧不可。2.4 へ                        |
| S3 オブジェクトあり + 署名付き URL 期限切れ | 15 分以内に再アクセスしていない          | 再生ボタンで新規 URL を発行             |
| Cycle 起動から 90 日経過                    | ライフサイクル削除済                     | HTTP 410 Gone が正常応答（Property 23） |

### 2.4 復旧（録音が再取得不可な場合）

Connect から録音を再取得する手段はない。以下を実施する。

1. オペレーターは対面 / 別チャネルで該当社員に再確認を行う。
2. Response テーブルを手動更新（要 SRE 承認）:

```powershell
aws dynamodb update-item `
  --profile AWS-security-check `
  --table-name "Response-<env>" `
  --key "{`"cycleId`":{`"S`":`"$cycleId`"},`"employeeId`":{`"S`":`"$employeeId`"}}" `
  --update-expression "SET voiceStatus = :v, callResultCode = :r, manuallyResolvedAt = :t, manuallyResolvedBy = :u" `
  --expression-attribute-values "{`":v`":{`"S`":`"UNREACHABLE`"},`":r`":{`"S`":`"RECORDING_LOST`"},`":t`":{`"S`":`"<ISO8601>`"},`":u`":{`"S`":`"<operator-sub>`"}}"
```

3. インシデントチケットを起票し、原因分析と対象範囲を記録する。

---

## 3. Transcript 欠落 / Transcribe 失敗 (Req 6.6)

### 3.1 検知

- アラーム: `safety-confirmation-transcribe-failed-<env>`
- Response の `callResultCode=TRANSCRIBE_FAILED`
- `TranscriptMetadata-<env>` テーブルに該当行が存在しない、または `confidence` フィールドが欠落

### 3.2 調査

```powershell
# Transcribe ジョブ一覧
aws transcribe list-transcription-jobs `
  --profile AWS-security-check `
  --status FAILED `
  --max-results 20

# 失敗ジョブの詳細
aws transcribe get-transcription-job `
  --profile AWS-security-check `
  --transcription-job-name "<job-name>"
```

### 3.3 失敗原因と対処

| FailureReason                               | 対処                                      |
| ------------------------------------------- | ----------------------------------------- |
| `Invalid sample rate. ...`                  | Connect 側録音設定の標本化周波数を確認    |
| `Unable to load audio file`                 | S3 オブジェクトの整合性確認、KMS 権限確認 |
| `Internal failure`                          | AWS 側障害。3.4 で手動再起動              |
| `The job exceeded the maximum job duration` | 通話時間が長すぎる。手動確認に切替        |

### 3.4 手動再起動

```powershell
$jobName = "manual-<cycleId>-<employeeId>-<unix>"
$mediaUri = "s3://safety-confirmation-recordings-<env>-<account>-<region>/recordings/$cycleId/$employeeId/$contactId.wav"

aws transcribe start-transcription-job `
  --profile AWS-security-check `
  --transcription-job-name $jobName `
  --language-code ja-JP `
  --media "MediaFileUri=$mediaUri" `
  --output-bucket-name "safety-confirmation-transcripts-<env>-<account>-<region>" `
  --output-key "transcripts/$cycleId/$employeeId/manual-$jobName.json" `
  --output-encryption-kms-key-id "alias/<env>-safety-confirmation"
```

完了後、Lambda `safety-confirmation-keyword-matcher-<env>` を手動 invoke して Voice_Status を確定するか、3.5 の手動確定を実施する。

### 3.5 手動確定（再 transcribe 不可な場合）

録音を聞いて手動で Voice_Status を判定し、Response を更新する。

```powershell
aws dynamodb update-item `
  --profile AWS-security-check `
  --table-name "Response-<env>" `
  --key "{`"cycleId`":{`"S`":`"$cycleId`"},`"employeeId`":{`"S`":`"$employeeId`"}}" `
  --update-expression "SET voiceStatus = :v, callResultCode = :r, manuallyResolvedAt = :t, manuallyResolvedBy = :u, manualNote = :n" `
  --expression-attribute-values "{`":v`":{`"S`":`"SAFE`"},`":r`":{`"S`":`"MANUAL_TRANSCRIBE`"},`":t`":{`"S`":`"<ISO8601>`"},`":u`":{`"S`":`"<operator-sub>`"},`":n`":{`"S`":`"manual review by operator`"}}"
```

---

## 4. SLA 違反 (Req 14.5 / Req 14.6)

### 4.1 検知

| 検知契機                         | アラーム                                      | 意味                      |
| -------------------------------- | --------------------------------------------- | ------------------------- |
| 30 分時点で初回発信完了率 < 100% | `safety-confirmation-sla-warning-30min-<env>` | Req 14.6 SLA 遅延警告通知 |
| 60 分時点でサイクル未完了        | `safety-confirmation-cycle-timeout-<env>`     | Req 14.5 タイムアウト通知 |

### 4.2 初動（30 分以内）

1. SPA「サイクル詳細」で「対象者総数 / 発信完了数 / 応答取得数 / 未到達数」を確認。
2. 未発信者の主な失敗要因をログから抽出:

```sql
-- CloudWatch Logs Insights: /aws/lambda/safety-confirmation-connect-dispatcher-<env>
fields @timestamp, @message
| filter @message like /<cycleId>/
| filter @message like /LimitExceededException/ or @message like /ERROR/
| stats count() as failures by bin(5m)
```

3. Connect 同時通話 10 上限の張り付きか、コードエラーかを切り分ける。

### 4.3 エスカレーション

| 経過時間  | エスカレーション先                 |
| --------- | ---------------------------------- |
| 0〜30 分  | L1 オペレーター                    |
| 30〜60 分 | L2 SRE                             |
| 60 分超   | L3 開発者 + 必要に応じて L4 経営層 |

### 4.4 事後分析

```sql
-- CycleFinalizer の発火タイムスタンプ
fields @timestamp, @message
| filter @logStream like /cycle-finalizer/
| filter @message like /<cycleId>/
| sort @timestamp asc

-- 個別発信完了タイムスタンプ
fields @timestamp, employeeId, callResultCode
| filter cycleId = '<cycleId>'
| stats min(@timestamp) as firstAttempt, max(@timestamp) as lastAttempt by employeeId
```

ボトルネック（Connect クォータ / Transcribe レイテンシ / Lambda コールドスタート）を特定し、ADR 起票 + Provisioned Concurrency 増強等の改善提案を行う。

---

## 5. 辞書誤更新の戻し方 (Req 8.5 / Req 8.7)

### 5.1 検知

- オペレーターから「Cycle の判定結果が想定と異なる」報告
- 監査ログ `/aws/safety-confirmation/audit-<env>` で `event=DICTIONARY_UPDATE` or `DICTIONARY_DELETE` を検索し、更新者と更新内容を特定

### 5.2 影響評価

- **進行中 Cycle**: Cycle 起動時点の辞書バージョンをスナップショットとして固定する設計（design.md「想定リスクと対策」/ Property 19）のため、進行中サイクルの判定への影響なし。
- **新規 Cycle**: 次回起動時から新バージョンが適用される。ロールバックは新規 Cycle 起動前に完了させる。

### 5.3 ロールバック実行

履歴テーブルから 1 つ前のバージョンを取得して再投入する。

```powershell
$env:PYTHONUTF8 = "1"
$badVersion = 18
$goodVersion = 17

# 履歴テーブルから旧バージョンを取得
aws dynamodb query `
  --profile AWS-security-check `
  --table-name "KeywordDictionaryHistory-<env>" `
  --key-condition-expression "version = :v" `
  --expression-attribute-values "{`":v`":{`"N`":`"$goodVersion`"}}" `
  --output json > .\dict-rollback.json

# 取得した旧バージョン全件を SPA「辞書管理」画面から再登録するか、
# 管理 API（DictionaryApi）の PATCH/POST で再投入する
# （DynamoDB 直接書込は監査ログを残さないため非推奨）
```

> **重要**: KeywordDictionary は管理 API 経由で更新すること。DynamoDB 直接更新は監査ログ（Req 8.7）に残らず、辞書バージョンも進まないため整合性が崩れる。

### 5.4 確認

1. 監査ログに `DICTIONARY_ADD` / `DICTIONARY_UPDATE` イベントが記録されていることを確認。
2. `KeywordDictionary-<env>` のメタレコード `version` がインクリメントされていることを確認。
3. 次回サイクル起動時に新バージョン（= 戻した内容）でスナップショットされることを確認。

---

## 6. Lambda 大量エラー（運用観測）

### 6.1 検知

- アラーム: `safety-confirmation-lambda-errors-<env>`
- 意味: アカウント内全 Lambda の合計 Errors が 5 分間に 5 件以上

### 6.2 関数特定

```sql
-- CloudWatch Logs Insights（複数 LogGroup を選択）
-- 対象: /aws/lambda/safety-confirmation-*
fields @timestamp, @log, @message
| filter @message like /ERROR/ or @message like /Task timed out/
| stats count() as errors by @log
| sort errors desc
```

エラー数の多い LogGroup から `@log` を特定し、当該 Lambda 単独のログで詳細を確認する。

### 6.3 対処

| エラー種別                               | 対処                                                  |
| ---------------------------------------- | ----------------------------------------------------- |
| `Task timed out after N seconds`         | Lambda Timeout 設定の見直し（CFn template 改修）      |
| `AccessDenied`                           | IAM Role の権限不足。Phase 6.x の Role 設計に立ち返る |
| `ResourceNotFoundException`              | Env Var 値ミスマッチ。CFn deploy が完了しているか確認 |
| `ThrottlingException`                    | DynamoDB / Connect クォータ。次第に解消する場合が多い |
| `ProvisionedThroughputExceededException` | 想定外の高負荷。CloudWatch メトリクスで継続監視       |

---

## 7. Cognito ロックアウト誤検知 / 解除

### 7.1 検知

- ユーザーから「ログインできない」報告
- `LockoutTable-<env>` で `userIdentifier=<email>` のレコードに失敗履歴 5 件が存在

### 7.2 解除

```powershell
aws dynamodb delete-item `
  --profile AWS-security-check `
  --table-name "Lockout-<env>" `
  --key "{`"userIdentifier`":{`"S`":`"<email>`"}}"
```

> TTL（最終失敗時刻 + 30 分）を待たずに即時解除する場合の手順。SRE 承認を必須とする。誤検知の頻度が高い場合は ADR 起票で根本対策を検討。

### 7.3 監査

`AuditLogGroup` で `event=AUTH_FAILURE_RECORDED` を検索し、攻撃の有無を確認。実 IP からの試行であればセキュリティチームへエスカレーション。

---

## 8. インバウンド未登録番号からの大量着信

### 8.1 検知

- アラーム: `safety-confirmation-inbound-unauthorized-<env>`
- 意味: 5 分間に 10 件以上の `INBOUND_CONTACT_RECEIVED && flow=NOT_REGISTERED`

### 8.2 調査

```sql
-- /aws/safety-confirmation/audit-<env>
fields @timestamp, callerPhoneMasked, flow
| filter event = "INBOUND_CONTACT_RECEIVED" and flow = "NOT_REGISTERED"
| stats count() as attempts by callerPhoneMasked
| sort attempts desc
| limit 20
```

`callerPhoneMasked` は `+8190****1234` のような末尾 4 桁マスク表記（Property 22）。発信元の傾向を確認する。

### 8.3 対処

- 単発の誤発信が多い場合: 経過観察
- 同一マスク表記から短期間に集中着信: Connect 側で発信元クォータ / ブロックリストを追加（変更チケット起票）
- 必要に応じて Out of Scope #11（声紋認証等）を ADR で再評価

---

## 9. 録音 / Transcript の 90 日経過前明示削除 (Req 15.5 / NFR3)

### 9.1 概要と適用範囲

社員本人または法定代理人から削除請求（個人情報保護法第 35 条、GDPR Right to Erasure 等）を受領し、**S3 ライフサイクル 90 日固定削除（[`privacy.md`](./privacy.md) §4 / Property 23）を待たずに録音 / Transcript を明示削除する必要が生じた場合**の手順を定める。

- 通常運用では本手順は使用しない（S3 Lifecycle Rule `DeleteRecordingsAfter90Days` / `DeleteTranscriptsAfter90Days` が 90 日固定で自動削除する）。
- 本手順は **法務承認を必須** とし、SRE が AWS Console / CLI で実行する（[`privacy.md`](./privacy.md) §6.3 / §11.6）。
- 対象データ：(a) S3 録音オブジェクト（D5）、(b) S3 Transcript オブジェクト（D6 本体）、(c) DynamoDB RecordingMetadata（D4）/ TranscriptMetadata（D6 メタ）の論理削除。

### 9.2 法務承認の取得

1. **削除請求の受領**：本人または法定代理人から書面 / メール / SPA フォーム等で請求を受領した時点で SRE / 法務 / プロダクト責任者の 3 者を CC した起票を作成。
2. **適用法令の特定**：個人情報保護法第 35 条（利用停止・消去）、GDPR Right to Erasure (Art. 17)、その他適用法令を法務が確認。
3. **削除対象範囲の確定**：対象社員 ID、対象 Cycle ID（複数可）、Cycle 起動から 90 日以内のものに限る（90 日経過済は既に S3 Lifecycle で自動削除済）。
4. **法務承認文書の作成**：請求者氏名 / 受領日 / 適用法令 / 削除対象範囲（cycleId × employeeId のリスト）/ 法務承認者署名を含む文書を起票し、法務責任者の承認を取得。
5. **承認文書の保管**：承認文書はインシデント記録の一部として §9.7 の方針で保管する。承認なしの削除実行は禁止。

> **重要**：本手順は不可逆操作のため、法務承認文書（紙 or PDF、署名入り）を取得するまで §9.3 以降に進まない。

### 9.3 対象 S3 オブジェクトの特定

DynamoDB D4 / D6 のメタレコードから S3 key を取得する。AWS CLI Profile 既定値は `AWS-security-check`、Region は `ap-northeast-1`（[`runbook.md`](./runbook.md) と整合）。

```powershell
$env:PYTHONUTF8 = "1"
$env:AWS_PROFILE = "AWS-security-check"  # 既定。Task 15.11 override に従う
$envName = "<env>"   # dev / stg / prod
$cycleId = "<cycle id>"
$employeeId = "<employee id>"

# (1) RecordingMetadata（D4）から録音 S3 key を取得
#     PK=cycleId / SK=employeeIdSeq（"{employeeId}#{seq:03d}" or "{employeeId}#INBOUND"）
aws dynamodb query `
  --table-name "RecordingMetadata-$envName" `
  --key-condition-expression "cycleId = :c AND begins_with(employeeIdSeq, :e)" `
  --expression-attribute-values "{`":c`":{`"S`":`"$cycleId`"},`":e`":{`"S`":`"$employeeId#`"}}" `
  --projection-expression "cycleId, employeeIdSeq, s3ObjectKey, s3Bucket" `
  --output json

# (2) TranscriptMetadata（D6 メタ）から Transcript S3 key を取得
aws dynamodb query `
  --table-name "TranscriptMetadata-$envName" `
  --key-condition-expression "cycleId = :c AND begins_with(employeeIdSeq, :e)" `
  --expression-attribute-values "{`":c`":{`"S`":`"$cycleId`"},`":e`":{`"S`":`"$employeeId#`"}}" `
  --projection-expression "cycleId, employeeIdSeq, transcriptS3Key, transcriptS3Bucket" `
  --output json
```

> **インバウンド請求の場合**：PK は `INBOUND#{contactId}` 形式となるため、`:c` を `"INBOUND#$contactId"` に置換して同じクエリを発行する。

取得した結果から、削除対象の `(s3Bucket, s3ObjectKey)` ペアと `(transcriptS3Bucket, transcriptS3Key)` ペアを法務承認文書に転記し、二重承認（SRE + 法務）を取ってから §9.4 に進む。

### 9.4 `aws s3api delete-object` 実行

> **不可逆操作の警告**：S3 Versioning は OFF（[`privacy.md`](./privacy.md) §3.3）のため、削除すると復元不可。法務承認文書の二重承認（§9.3 末尾）を取得済であることを再確認してから実行する。

```powershell
$accountId = "<aws-account-id>"
$region = "ap-northeast-1"
$recordingsBucket = "safety-confirmation-recordings-$envName-$accountId-$region"
$transcriptsBucket = "safety-confirmation-transcripts-$envName-$accountId-$region"

# (1) 録音オブジェクト削除
$recordingKey = "<recordings/{cycleId}/{employeeId}/{seq:03d}.wav から取得>"
aws s3api delete-object `
  --bucket $recordingsBucket `
  --key $recordingKey

# (2) Transcript オブジェクト削除
$transcriptKey = "<transcripts/{cycleId}/{employeeId}/{seq:03d}.json から取得>"
aws s3api delete-object `
  --bucket $transcriptsBucket `
  --key $transcriptKey

# (3) 削除後の不在確認（404 Not Found が返れば成功）
aws s3api head-object --bucket $recordingsBucket --key $recordingKey
aws s3api head-object --bucket $transcriptsBucket --key $transcriptKey
```

**SSE-KMS 暗号化された S3 オブジェクトの削除に関する注意点**：

- バケット / オブジェクトともに `alias/<env>-safety-confirmation` CMK で暗号化されているが、**DeleteObject 操作には `kms:Decrypt` 権限は不要**（メタデータ操作のみ）。
- ただし削除実行者（SRE Federated Role 等）は `s3:DeleteObject` 権限を保持していること。`AWS-security-check` Profile は Phase 12 で必要権限を整備済（[`runbook.md`](./runbook.md) 参照）。
- マルチパートアップロード中の残置オブジェクトがある場合は `aws s3api list-multipart-uploads` で確認し、`abort-multipart-upload` で中断する（通常は Connect / Transcribe からのアップロード完了済のため対象なし）。

### 9.5 DynamoDB メタの論理削除

S3 オブジェクト削除後、D4 / D6 のメタレコードに **論理削除フラグを付与する**（物理削除はしない）。

**方針判断（論理削除を採用する理由）**：

- (a) Recording API（[`design.md`](../../.kiro/specs/safety-confirmation-system/design.md) Recording_Store）の署名付き URL 発行ロジックが「メタ存在 + S3 存在」の組合せで挙動を分けており、メタ物理削除すると 404 と 410 Gone（90 日経過済）の判別が壊れる。
- (b) Cycle（D2）/ Response（D3）は無期限保持（[`privacy.md`](./privacy.md) §4）であり、過去 Cycle 履歴の追跡性を保つには `(cycleId, employeeId)` 紐付けのメタは残すべき。
- (c) 監査追跡性（誰の請求で・いつ・誰が削除したか）をメタ本体で追えるようにする。

```powershell
$nowIso = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$deletedBy = "<sre-cognito-sub or operator-id>"
$reason = "RECORDING_DELETE_BY_REQUEST"  # または "RECORDING_DELETE_MANUAL"
$legalApprovalRef = "<法務承認文書 ID>"

# (1) RecordingMetadata に削除フラグを付与
aws dynamodb update-item `
  --table-name "RecordingMetadata-$envName" `
  --key "{`"cycleId`":{`"S`":`"$cycleId`"},`"employeeIdSeq`":{`"S`":`"$employeeId#001`"}}" `
  --update-expression "SET deleted = :d, deletedAt = :t, deletedBy = :u, deletionReason = :r, legalApprovalRef = :a" `
  --expression-attribute-values "{`":d`":{`"BOOL`":true},`":t`":{`"S`":`"$nowIso`"},`":u`":{`"S`":`"$deletedBy`"},`":r`":{`"S`":`"$reason`"},`":a`":{`"S`":`"$legalApprovalRef`"}}"

# (2) TranscriptMetadata にも同様に削除フラグを付与
aws dynamodb update-item `
  --table-name "TranscriptMetadata-$envName" `
  --key "{`"cycleId`":{`"S`":`"$cycleId`"},`"employeeIdSeq`":{`"S`":`"$employeeId#001`"}}" `
  --update-expression "SET deleted = :d, deletedAt = :t, deletedBy = :u, deletionReason = :r, legalApprovalRef = :a" `
  --expression-attribute-values "{`":d`":{`"BOOL`":true},`":t`":{`"S`":`"$nowIso`"},`":u`":{`"S`":`"$deletedBy`"},`":r`":{`"S`":`"$reason`"},`":a`":{`"S`":`"$legalApprovalRef`"}}"
```

> **注意**：複数 seq（再発信が発生した cycle）の場合は §9.3 のクエリ結果の `employeeIdSeq` 全件に対して上記 update-item を繰り返す。インバウンド分は SK が `{employeeId}#INBOUND` 形式となる。

### 9.6 監査ログの手動投入

`shared.audit.logger.write_audit_log`（[`backend/shared/audit/logger.py`](../../backend/shared/audit/logger.py)）と同一スキーマの JSON 行を AuditLogGroup `/aws/safety-confirmation/audit-<env>` に直接 `put-log-events` で投入する。Lambda 経由ではなく CLI で投入するため、LogStream は `manual/<YYYY-MM-DD>` で別建てとする。

**イベント種別の使い分け**：

| `event` 値                    | 使用ケース                                                                       |
| ----------------------------- | -------------------------------------------------------------------------------- |
| `RECORDING_DELETE_BY_REQUEST` | 本人 / 法定代理人からの削除請求に基づく明示削除（§9.2 法務承認あり、通常ケース） |
| `RECORDING_DELETE_MANUAL`     | 法務判断 / 訴訟対応等の業務上必要性に基づく明示削除（請求なし、SRE 主導）        |

```powershell
$auditLogGroup = "/aws/safety-confirmation/audit-$envName"
$today = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
$manualStream = "manual/$today"
$epochMs = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())

# (1) LogStream を作成（既存ならスキップされる）
aws logs create-log-stream `
  --log-group-name $auditLogGroup `
  --log-stream-name $manualStream 2>$null

# (2) 監査ログ 1 件投入（録音削除）
$recordRecording = @{
  event = "RECORDING_DELETE_BY_REQUEST"  # または RECORDING_DELETE_MANUAL
  timestamp = $nowIso
  principal = $deletedBy
  target = "$cycleId#$employeeId#recording"
  outcome = "SUCCESS"
  s3Bucket = $recordingsBucket
  s3ObjectKey = $recordingKey
  legalApprovalRef = $legalApprovalRef
  deletionReason = "本人請求 / 個人情報保護法第 35 条"
} | ConvertTo-Json -Compress

aws logs put-log-events `
  --log-group-name $auditLogGroup `
  --log-stream-name $manualStream `
  --log-events "[{`"timestamp`":$epochMs,`"message`":$($recordRecording | ConvertTo-Json)}]"

# (3) 監査ログ 1 件投入（Transcript 削除）
$recordTranscript = @{
  event = "RECORDING_DELETE_BY_REQUEST"
  timestamp = $nowIso
  principal = $deletedBy
  target = "$cycleId#$employeeId#transcript"
  outcome = "SUCCESS"
  s3Bucket = $transcriptsBucket
  s3ObjectKey = $transcriptKey
  legalApprovalRef = $legalApprovalRef
  deletionReason = "本人請求 / 個人情報保護法第 35 条"
} | ConvertTo-Json -Compress

aws logs put-log-events `
  --log-group-name $auditLogGroup `
  --log-stream-name $manualStream `
  --log-events "[{`"timestamp`":$epochMs,`"message`":$($recordTranscript | ConvertTo-Json)}]"
```

**監査ログのスキーマ準拠**：必須フィールドは `event` / `timestamp` (ISO 8601 UTC, 末尾 `Z`) / `principal` / `target` / `outcome` の 5 つ（[`backend/shared/audit/logger.py`](../../backend/shared/audit/logger.py) の `_RESERVED_KEYS` と一致）。追加フィールド（`s3Bucket` / `s3ObjectKey` / `legalApprovalRef` / `deletionReason`）は `extra` 相当の任意キーとして並置する。

**投入確認**：

```powershell
# CloudWatch Logs Insights で投入結果を確認
aws logs start-query `
  --log-group-name $auditLogGroup `
  --start-time ([DateTimeOffset]::UtcNow.AddMinutes(-10).ToUnixTimeSeconds()) `
  --end-time ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) `
  --query-string "fields @timestamp, event, principal, target, legalApprovalRef | filter event like /RECORDING_DELETE/ | sort @timestamp desc | limit 10"
```

### 9.7 インシデント記録の保管

法務承認文書 + 削除実施記録は以下の構成でセットとして保管する。

| 保管対象                                                      | 保管先                                                                  | 保管期間                                                                         |
| ------------------------------------------------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 法務承認文書（PDF、署名入り）                                 | 法務部門の文書管理システム + SRE 共有ストレージ（暗号化保管）           | **7 年**                                                                         |
| 削除実施記録（CLI 実行ログ、削除前後の `head-object` 出力等） | SRE インシデントチケット + 法務部門共有                                 | **7 年**                                                                         |
| 監査ログ（§9.6 で投入した JSON 行）                           | CloudWatch Logs `/aws/safety-confirmation/audit-<env>`                  | `LogRetentionDays` 設定値（既定 90 日。長期保管が必要なら S3 Export を別途設計） |
| DynamoDB の `deleted=true` メタレコード                       | RecordingMetadata / TranscriptMetadata 上に永続保持（Cycle 寿命と同等） | 無期限                                                                           |

**保管期間 7 年の根拠**：個人情報保護法に基づく開示請求対応の証跡として、商法上の帳簿類保存期間（10 年）と税法上の証憑保存期間（7 年）の短い方を採用。法務部門の保管期間規程が異なる場合はそちらに合わせる。

**監査ログの長期保管が必要な場合**：CloudWatch Logs の保持期間（既定 90 日）を超えた追跡が必要な場合は、AuditLogGroup を S3 Export タスクで別バケットに永続化する設計を検討（本タスクのスコープ外、ADR で起票）。

### 9.8 関連ドキュメント相互参照

- 個人請求受領フロー全体：[`privacy.md`](./privacy.md) §6.3
- 90 日固定削除の根拠と保管期間表：[`privacy.md`](./privacy.md) §4
- 削除依頼の監査ログ要件：[`privacy.md`](./privacy.md) §6.4
- 監査ログの仕様 / マスキング：[`privacy.md`](./privacy.md) §9 + Property 22
- プライバシーチェックリスト（明示削除手順）：[`privacy.md`](./privacy.md) §11.6
- 通常運用 / 一次対応との接続：[`runbook.md`](./runbook.md)
- CloudWatch アラーム / Logs 構成：[`monitoring.md`](./monitoring.md)

---

## 改訂履歴

| 日付       | 改訂内容                                                                                                                                                                          | 起票者 |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| 2026-06-28 | 初版作成（Task 15.4、想定インシデント 8 件カバー）                                                                                                                                | kiro   |
| 2026-06-28 | §9「録音 / Transcript の 90 日経過前明示削除」追加（Task 15.13、関連要件に Req 15.5 を追加）                                                                                      | kiro   |
| 2026-06-29 | §1.4 / §2.2 / §3.4 / §9.4 / §9.6 のテーブル名・バケット名・KMS Alias・AuditLogGroup 名を実装真値（template.yaml）に統一（Task 15.17、不一致 9 件、§9.6 AuditLogGroup は実害修正） | kiro   |
