# 安否確認システム 監視リファレンス

- **対象読者**: オペレーター、SRE、開発者
- **関連要件**: Req 6.6 / Req 10.9 / Req 14.5 / Req 14.6 / Req 16.x / NFR2
- **関連設計**: Phase 12 観測・監視レイヤ
- **前提**: アラーム発火時の運用手順は [`runbook.md`](./runbook.md) §5、復旧手順は [`incident-response.md`](./incident-response.md) を参照。

> 本ドキュメントは `infrastructure/template.yaml` の `AWS::CloudWatch::Alarm` / `AWS::Logs::MetricFilter` / `AWS::SNS::Topic` / `AWS::SQS::Queue` リソースを真として整理する。

---

## 1. メトリクス Namespace 統一規約

| Namespace            | 用途                                                                            | 発行元                                                                |
| -------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `SafetyConfirmation` | アプリケーション独自メトリクス（カスタム）                                      | `CycleFinalizerFn` の `put_metric_data` / `InboundUnauthorizedFilter` |
| `AWS/Lambda`         | Lambda 標準メトリクス（Errors / Throttles / Duration）                          | AWS マネージド                                                        |
| `AWS/SQS`            | SQS 標準メトリクス（NumberOfMessagesSent / ApproximateNumberOfMessagesVisible） | AWS マネージド                                                        |

`SafetyConfirmation` Namespace の MetricName 命名規則:

- PascalCase
- 単位ではなく事象を表す名前（例: `CycleTimeout` ⭕ / `CycleTimeoutCount` ❌）
- Dimensions は最小限。サイクル別 / Lambda 関数別の集約はせず、メトリクス値 = 発生件数とする

実装上の MetricName 一覧（Phase 12 / セッション 13 副次対応で統一済）:

| MetricName            | Value 意味                                                  | 発火タイミング                                                      |
| --------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------- |
| `SlaWarning30Min`     | 30 分時点で初回発信完了率 100% 未達のサイクル数（1 回 = 1） | `CycleFinalizerFn` が `TIMER_30MIN` EventBridge イベントで発火      |
| `CycleTimeout`        | 60 分タイムアウト確定サイクル数（1 回 = 1）                 | `CycleFinalizerFn` が `TIMER_60MIN` EventBridge イベントで発火      |
| `InboundUnauthorized` | 未登録番号からの着信件数                                    | `InboundUnauthorizedFilter` (Metric Filter) が AuditLogGroup を集計 |

---

## 2. CloudWatch アラーム一覧

`infrastructure/template.yaml` 内 6 件 + Metric Filter 1 件。全アラームは `OperatorTopic` に通知。

### 2.1 SLAWarning30MinAlarm — Req 14.6 SLA 遅延警告

| 項目               | 値                                            |
| ------------------ | --------------------------------------------- |
| AlarmName          | `safety-confirmation-sla-warning-30min-<env>` |
| Namespace          | `SafetyConfirmation`                          |
| MetricName         | `SlaWarning30Min`                             |
| Dimensions         | （なし、サイクル集約）                        |
| Statistic          | `Sum`                                         |
| Period             | 300 秒                                        |
| EvaluationPeriods  | 1                                             |
| Threshold          | 1                                             |
| ComparisonOperator | `GreaterThanOrEqualToThreshold`               |
| TreatMissingData   | `notBreaching`                                |
| AlarmActions       | `!Ref OperatorTopic`                          |

**推奨対応**: [`runbook.md` §5.1](./runbook.md#51-30-分-sla-遅延警告-req-146) で未発信者リストを抽出し、Connect クォータ張り付き or コードエラーを切り分ける。

### 2.2 CycleTimeoutAlarm — Req 14.5 タイムアウト通知

| 項目               | 値                                        |
| ------------------ | ----------------------------------------- |
| AlarmName          | `safety-confirmation-cycle-timeout-<env>` |
| Namespace          | `SafetyConfirmation`                      |
| MetricName         | `CycleTimeout`                            |
| Dimensions         | （なし）                                  |
| Statistic          | `Sum`                                     |
| Period             | 300 秒                                    |
| EvaluationPeriods  | 1                                         |
| Threshold          | 1                                         |
| ComparisonOperator | `GreaterThanOrEqualToThreshold`           |
| TreatMissingData   | `notBreaching`                            |
| AlarmActions       | `!Ref OperatorTopic`                      |

**推奨対応**: [`runbook.md` §4.2](./runbook.md#42-通知受信後のオペレーター作業30-分以内) でタイムアウト確定者の対面確認を実施。EventBridge ルール `cycle-60min-<cycleId>` が発火しない場合は [`runbook.md` §4.3](./runbook.md#43-eventbridge-ルールが発火しない場合異常系) へ。

### 2.3 LambdaErrorsAlarm — 運用観測

| 項目               | 値                                        |
| ------------------ | ----------------------------------------- |
| AlarmName          | `safety-confirmation-lambda-errors-<env>` |
| Namespace          | `AWS/Lambda`                              |
| MetricName         | `Errors`                                  |
| Dimensions         | （なし、アカウント全 Lambda 集約）        |
| Statistic          | `Sum`                                     |
| Period             | 300 秒                                    |
| EvaluationPeriods  | 1                                         |
| Threshold          | 5                                         |
| ComparisonOperator | `GreaterThanOrEqualToThreshold`           |
| TreatMissingData   | `notBreaching`                            |
| AlarmActions       | `!Ref OperatorTopic`                      |

**設計判断**: Dimensions なし = アカウント内全 Lambda を集約。関数特定は CloudWatch Logs Insights で別途トリアージ（運用効率優先）。

**推奨対応**: [`incident-response.md` §6](./incident-response.md#6-lambda-大量エラー運用観測) で当該 Lambda を特定して個別対応。

### 2.4 RecordingUploadFailureAlarm — Req 10.9

| 項目               | 値                                                       |
| ------------------ | -------------------------------------------------------- |
| AlarmName          | `safety-confirmation-recording-upload-failure-<env>`     |
| Namespace          | `AWS/SQS`                                                |
| MetricName         | `NumberOfMessagesSent`                                   |
| Dimensions         | `QueueName=safety-confirmation-recording-meta-dlq-<env>` |
| Statistic          | `Sum`                                                    |
| Period             | 300 秒                                                   |
| EvaluationPeriods  | 1                                                        |
| Threshold          | 1                                                        |
| ComparisonOperator | `GreaterThanOrEqualToThreshold`                          |
| TreatMissingData   | `notBreaching`                                           |
| AlarmActions       | `!Ref OperatorTopic`                                     |

**意味**: `RecordingMetadataWriterFn` が 3 連敗して DLQ にメッセージが滞留。

**推奨対応**: [`incident-response.md` §1](./incident-response.md#1-dlq-メッセージ滞留-req-109) で DLQ 内容を peek → 失敗原因特定 → 再ドライブ。

### 2.5 TranscribeFailedAlarm — Req 6.6

| 項目               | 値                                                          |
| ------------------ | ----------------------------------------------------------- |
| AlarmName          | `safety-confirmation-transcribe-failed-<env>`               |
| Namespace          | `AWS/Lambda`                                                |
| MetricName         | `Errors`                                                    |
| Dimensions         | `FunctionName=safety-confirmation-transcribe-starter-<env>` |
| Statistic          | `Sum`                                                       |
| Period             | 300 秒                                                      |
| EvaluationPeriods  | 1                                                           |
| Threshold          | 1                                                           |
| ComparisonOperator | `GreaterThanOrEqualToThreshold`                             |
| TreatMissingData   | `notBreaching`                                              |
| AlarmActions       | `!Ref OperatorTopic`                                        |

**設計判断**: `TranscribeStarterFn` は 3 連敗で `raise` する設計のため、`AWS/Lambda Errors` メトリクスを `FunctionName` Dimension で絞って監視。**DLQ は持たない**（実装側を真とする）。

**推奨対応**: [`incident-response.md` §3](./incident-response.md#3-transcript-欠落--transcribe-失敗-req-66) で失敗ジョブを特定 → 手動再起動 or 手動確定。

### 2.6 InboundUnauthorizedAlarm — Out of Scope 9 警戒

| 項目               | 値                                                    |
| ------------------ | ----------------------------------------------------- |
| AlarmName          | `safety-confirmation-inbound-unauthorized-<env>`      |
| Namespace          | `SafetyConfirmation`                                  |
| MetricName         | `InboundUnauthorized`                                 |
| Dimensions         | （なし）                                              |
| Statistic          | `Sum`                                                 |
| Period             | 300 秒                                                |
| EvaluationPeriods  | 1                                                     |
| Threshold          | 10                                                    |
| ComparisonOperator | `GreaterThanOrEqualToThreshold`                       |
| TreatMissingData   | `notBreaching`                                        |
| AlarmActions       | `!Ref OperatorTopic`                                  |
| DependsOn          | `InboundUnauthorizedFilter` (AWS::Logs::MetricFilter) |

**意味**: 5 分間に未登録番号着信が 10 件以上 = brute force 兆候。

**推奨対応**: [`incident-response.md` §8](./incident-response.md#8-インバウンド未登録番号からの大量着信) で発信元の傾向を確認。

### 2.7 InboundUnauthorizedFilter (Metric Filter)

| 項目            | 値                                                                      |
| --------------- | ----------------------------------------------------------------------- |
| FilterName      | `safety-confirmation-inbound-unauthorized-<env>`                        |
| LogGroup        | `AuditLogGroup` (`/aws/safety-confirmation/audit-<env>`)                |
| FilterPattern   | `{ $.event = "INBOUND_CONTACT_RECEIVED" && $.flow = "NOT_REGISTERED" }` |
| MetricNamespace | `SafetyConfirmation`                                                    |
| MetricName      | `InboundUnauthorized`                                                   |
| MetricValue     | `1`                                                                     |
| DefaultValue    | `0`                                                                     |

`InboundHandlerFn` が監査ログ（Req 16.3）に書込む構造化 JSON を集計してメトリクス化。

---

## 3. SNS 通知配線

| リソース                    | 値                                                                                        |
| --------------------------- | ----------------------------------------------------------------------------------------- |
| `OperatorTopic`             | `arn:aws:sns:ap-northeast-1:<account>:safety-confirmation-operator-<env>`                 |
| `OperatorEmailSubscription` | Protocol=`email` / Endpoint=`!Ref OperatorEmail` パラメータ                               |
| 通知パターン                | 全 6 アラーム + 1 件の Metric Filter 経由アラーム = 7 経路すべてが `OperatorTopic` に集約 |

OperatorEmail の確認手順は [`runbook.md` §7.2](./runbook.md#72-sns-subscription-更新operatoremail-変更時) を参照。

---

## 4. DLQ 構成

| Queue                                          | 用途                                         | 設定                                                                    |
| ---------------------------------------------- | -------------------------------------------- | ----------------------------------------------------------------------- |
| `safety-confirmation-recording-meta-dlq-<env>` | `RecordingMetadataWriterFn` 失敗イベント保管 | MessageRetentionPeriod=1,209,600 秒（14 日）/ SqsManagedSseEnabled=true |

DLQ Outputs:

- `RecordingMetadataWriterDLQArn` (Export `<stack>-RecordingMetadataWriterDLQArn`)
- `RecordingMetadataWriterDLQUrl` (Export `<stack>-RecordingMetadataWriterDLQUrl`)

> `TranscribeStarterFn` には DLQ なし。3 連敗 → `raise` → `AWS/Lambda Errors` 経由で `TranscribeFailedAlarm` が発火する設計（Phase 6.4 / 12.6）。

---

## 5. ログ構成

| LogGroup                                          | 用途                                                                      | RetentionInDays                       |
| ------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------- |
| `/aws/lambda/safety-confirmation-<purpose>-<env>` | Lambda 関数別ログ（全 19 関数）                                           | `!Ref LogRetentionDays`（既定 90 日） |
| `/aws/safety-confirmation/cycle-<env>`            | Step Functions 実行ログ                                                   | 同上                                  |
| `/aws/safety-confirmation/audit-<env>`            | 監査ログ（AuditLogGroup、Req 16.3）。`write_audit_log` で 6 Lambda が出力 | 同上                                  |

監査ログには電話番号がマスキング表記（`+8190****1234`、Property 22）で記録される。`mask_phone` 経由（Phase 12.7）。

---

## 6. ダッシュボード推奨構成（オプション）

CloudWatch Dashboard を作成する場合の推奨ウィジェット（CFn 化未実施、運用者が CloudWatch コンソールで作成）:

| ウィジェット                                    | メトリクス                                                                      |
| ----------------------------------------------- | ------------------------------------------------------------------------------- |
| サイクル SLA（過去 24h）                        | `SafetyConfirmation.SlaWarning30Min` + `SafetyConfirmation.CycleTimeout`        |
| Lambda エラー（過去 6h、関数別 Stacked Area）   | `AWS/Lambda.Errors` for each safety-confirmation-\* function                    |
| Lambda Duration p99（過去 6h、関数別）          | `AWS/Lambda.Duration` `p99`                                                     |
| DLQ メッセージ数（過去 24h）                    | `AWS/SQS.ApproximateNumberOfMessagesVisible` Dim=DLQ QueueName                  |
| 監査イベント発生数（過去 6h、種別 Stacked Bar） | `MetricFilter`（InboundUnauthorized）+ 追加で `DICTIONARY_*` 等の Filter を作成 |
| Connect 同時通話数                              | `AWS/Connect.CallsBreachingConcurrencyQuota` （Connect 側で別管理）             |

---

## 7. アラーム発火時の初動チェックリスト

| アラーム名                                           | 一次対応                                                   | 復旧手順                                                                                 |
| ---------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `safety-confirmation-sla-warning-30min-<env>`        | [runbook §5.1](./runbook.md#51-30-分-sla-遅延警告-req-146) | [incident-response §4](./incident-response.md#4-sla-違反-req-145--req-146)               |
| `safety-confirmation-cycle-timeout-<env>`            | [runbook §5.2](./runbook.md#52-60-分タイムアウト-req-145)  | [incident-response §4](./incident-response.md#4-sla-違反-req-145--req-146)               |
| `safety-confirmation-recording-upload-failure-<env>` | [runbook §5.3](./runbook.md#53-dlq-通知)                   | [incident-response §1](./incident-response.md#1-dlq-メッセージ滞留-req-109)              |
| `safety-confirmation-lambda-errors-<env>`            | [runbook §5.4](./runbook.md#54-lambda-大量エラー)          | [incident-response §6](./incident-response.md#6-lambda-大量エラー運用観測)               |
| `safety-confirmation-transcribe-failed-<env>`        | [runbook §5.5](./runbook.md#55-transcribe-失敗)            | [incident-response §3](./incident-response.md#3-transcript-欠落--transcribe-失敗-req-66) |
| `safety-confirmation-inbound-unauthorized-<env>`     | [runbook §5.6](./runbook.md#56-未登録番号インバウンド連発) | [incident-response §8](./incident-response.md#8-インバウンド未登録番号からの大量着信)    |

---

## 8. 確認用コマンドリファレンス

```powershell
$env:PYTHONUTF8 = "1"

# アラーム一覧 + 現在状態
aws cloudwatch describe-alarms `
  --profile AWS-security-check `
  --alarm-name-prefix safety-confirmation `
  --query "MetricAlarms[].{Name:AlarmName,State:StateValue,Reason:StateReason}" `
  --output table

# アラーム履歴（過去 24h）
aws cloudwatch describe-alarm-history `
  --profile AWS-security-check `
  --alarm-name safety-confirmation-cycle-timeout-<env> `
  --history-item-type StateUpdate `
  --start-date "<ISO8601 24h ago>" `
  --max-records 50

# SNS Subscription 確認
$topicArn = aws cloudformation describe-stacks `
  --profile AWS-security-check `
  --stack-name safety-confirmation-<env> `
  --query "Stacks[0].Outputs[?OutputKey=='OperatorTopicArn'].OutputValue" --output text
aws sns list-subscriptions-by-topic `
  --profile AWS-security-check `
  --topic-arn $topicArn

# DLQ 滞留数
$dlqUrl = aws cloudformation describe-stacks `
  --profile AWS-security-check `
  --stack-name safety-confirmation-<env> `
  --query "Stacks[0].Outputs[?OutputKey=='RecordingMetadataWriterDLQUrl'].OutputValue" --output text
aws sqs get-queue-attributes `
  --profile AWS-security-check `
  --queue-url $dlqUrl `
  --attribute-names ApproximateNumberOfMessages OldestMessageAge
```

---

## 改訂履歴

| 日付       | 改訂内容                                                               | 起票者 |
| ---------- | ---------------------------------------------------------------------- | ------ |
| 2026-06-28 | 初版作成（Task 15.4、template.yaml の 6 Alarm + Metric Filter に整合） | kiro   |
