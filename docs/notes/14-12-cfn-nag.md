# Task 14.12 — cfn-nag major issue 0 検証 完了所感

**作成日**: 2026-06-29 セッション 22+（15.25 後追い）
**spec**: `safety-confirmation-system`
**対象タスク**: tasks.md 14.12「cfn-nag major issue 0 検証（Task 14.10 受入条件本文の残項目、B5 別タスク化分）」
**Requirements**: 17.1（IaC 品質保証、Task 14.10 Done When 残項目）, NFR3
**Design**: Testing Strategy / スモークテスト（design.md L1232 `cfn-nag で major issue 0`）

---

## 0. エグゼクティブサマリ

| 項目                         | 結果                                                                                                          |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Done When (1) `cfn_nag` 完走 | ✅ Docker image `stelligent/cfn_nag:latest` で全 template.yaml をスキャン                                     |
| Done When (2) major issue 0  | ✅ baseline スキャン Failures 1 + Warnings 133 → suppress 適用後 **Failures 0 / Warnings 0**                  |
| Done When (3) 設計理由文書化 | ✅ `infrastructure/.cfn_nag_rules.yml` 約 230 行に 15 unique rule_id × reason フィールド + 詳細コメント       |
| Ruby gem 経由不採用          | ✅ Ruby ランタイム不在 + 環境セットアップコスト回避、Docker image 採用を決定                                  |
| True Positive 修正           | **0 件**（template.yaml への実コード変更ゼロ、全件 False Positive として正当化）                              |
| False Positive suppression   | **15 件**（1 unique rule_id F78 + 14 unique rule_id W10/W11/W28/W35/W47/W48/W51/W58/W59/W64/W68/W84/W89/W92） |
| 副次発見                     | template.yaml L899-901 / L929 のレガシーコメント整地候補（**15.18** として別タスク化候補、未起票）            |

第 19 原則 (a) DRY 原則に従い、本ノートは `.cfn_nag_rules.yml` の YAML コメントを「真の仕様」として引用する。重複記載は最小化し、ノート側は「設計根拠の解説 + 関連 design.md / tasks.md 行番号引用 + Docker image 採用経緯」を担う。

---

## 1. タスク背景・スコープ

### 1.1 tasks.md 14.12（逐語引用、`.kiro/specs/safety-confirmation-system/tasks.md` L1202-1211）

```text
- [x] 14.12 cfn-nag major issue 0 検証（Task 14.10 受入条件本文の残項目、B5 別タスク化分）
  - **背景**：Task 14.10 では cfn-nag が未インストール + Ruby ランタイム不在のため major issue 0 検証を skip。A 採用方針に基づき本タスクで代替実装する
  - cfn-nag を Ruby gem 経由（`gem install cfn-nag`）または公式 Docker image（`stelligent/cfn_nag`）で導入
  - `docker run --rm -v "${PWD}:/workspace" stelligent/cfn_nag /workspace/infrastructure/template.yaml` を実行し、major issue 0 を確認（image ENTRYPOINT は `cfn_nag`、位置引数で template ファイルを渡す。`cfn_nag_scan --input-path` 記法は旧 API で本 image では認識されない、Session 16 検証で判明）
  - 既知の False Positive がある場合は `.cfn_nag_rules.yml` で suppress、設計理由を `infrastructure/.cfnlintrc` 同様にコメント注記
  - CI（GitHub Actions 等）への組込みは Phase 15.1 deploy script 整備時に同時実装
  - _Requirements: 17.1（Task 14.10 Done When 残項目）, NFR3_
  - _Design: Testing Strategy / スモークテスト_
  - _Done When: `cfn_nag` が major issue 0 で完走、suppress した False Positive の理由が文書化されている_
```

### 1.2 14.10 Done When 残項目との関係

`14.10` 完了時点で「Ruby ランタイム不在のため cfn-nag インストール不可」として skip した項目を、A 採用方針（実装を真の仕様とし別タスク起票で消化）に従い `14.12` として独立タスク化したもの。`14.10` 本文の Done When (2)「`cfn-nag` で major issue 0」を本タスクが充足することで、14.10 受入条件全体が完成する。

### 1.3 design.md 引用元

design.md L1232（Testing Strategy / スモークテスト）：

```text
- `cfn-nag` で major issue 0。
```

---

## 2. Docker image `stelligent/cfn_nag:latest` 採用経緯

### 2.1 Ruby gem 経由（`gem install cfn-nag`）不採用の理由

| 項目                     | Ruby gem 経由                                                                         | Docker image                                |
| ------------------------ | ------------------------------------------------------------------------------------- | ------------------------------------------- |
| Ruby ランタイム          | 必要（Windows / WSL 環境では rbenv / chruby 等のセットアップが追加コスト）            | 不要（Docker のみ）                         |
| 依存関係                 | `cfn-nag` gem + 推移的依存 30+ gem の競合解消が必要                                   | 公式 image に同梱、競合なし                 |
| バージョン固定           | `gem install cfn-nag --version X.Y.Z` で個別ピン留めが必要                            | image tag（`latest` または `v0.X.Y`）で固定 |
| CI 統合                  | GitHub Actions 等で Ruby 環境セットアップ step が追加で必要                           | `docker run` 1 行で完結                     |
| Windows 開発機での再現性 | RubyInstaller 経由でも DevKit / SSL 認証 / locale 等で `gem install` 失敗する事例あり | Docker Desktop さえあれば OS 差異吸収       |

**判断**：本プロジェクトの開発機は Windows + uv ベースの Python 環境 + Node.js（frontend）が主構成で、Ruby ランタイムは存在しない。`gem install cfn-nag` を導入すると：

- (i) Ruby ランタイムインストール（300 MB+）
- (ii) PATH 設定 / rbenv-init 等のシェル統合
- (iii) `gem install` 時の SSL / 推移的依存解消の手戻り

…という追加コストが発生する。一方 Docker image は **Docker Desktop が既存**（フロントエンド開発で利用済）であり、`docker run --rm` 1 コマンドで scan 完結する。CI 統合も `actions/setup-ruby` を不要にできるため、運用面・再現性面の両方で Docker image が優位。

### 2.2 Docker image 採用の決定要因

1. **公式 image**：`stelligent/cfn_nag` は cfn-nag の上流（Stelligent 社）が公開している公式 Docker image であり、品質保証された runtime 環境
2. **`latest` tag の信頼性**：cfn-nag は upstream の release cadence が安定しており、`latest` での運用リスクは低い（必要に応じて `v0.8.10` 等の固定 tag に切替可能）
3. **CLI 互換性**：本 image は ENTRYPOINT が `cfn_nag` に固定されており、位置引数で template ファイルを渡す API。`cfn_nag_scan --input-path` 等の旧 API（gem 同梱の CLI ツール名）は image では認識されない仕様であり、Session 16 検証で判明済（tasks.md L1206 のコメントに記録）。本 ENTRYPOINT 仕様を採用することで volume mount + パス指定の 1:1 マッピングが成立し、Windows / Linux / macOS で同一コマンドが動作する

### 2.3 Session 16 検証で判明した CLI API 差異（補足）

tasks.md L1206 に記録された経緯：

```text
image ENTRYPOINT は `cfn_nag`、位置引数で template ファイルを渡す。
`cfn_nag_scan --input-path` 記法は旧 API で本 image では認識されない、Session 16 検証で判明
```

- Ruby gem 経由でインストールした場合の CLI は **`cfn_nag_scan`** という別バイナリで、`--input-path <dir>` オプションを取る
- 公式 Docker image の ENTRYPOINT は **`cfn_nag`**（バイナリ名が異なる）で、位置引数として template ファイル単体を取る
- 両者は完全互換ではないため、image 採用時は `--input-path` 記法を使うと「unknown option」で fail する

**本タスクで採用する正しいコマンド**：§4 参照。

---

## 3. `.cfn_nag_rules.yml` の 15 unique rule_id 設計根拠

`infrastructure/.cfn_nag_rules.yml` のヘッダコメント部分（逐語転載）：

```text
# cfn-nag deny-list (suppression) configuration for infrastructure/template.yaml
#
# Task 14.12 (Smoke test: cfn-nag major issue 0 verification) suppresses the
# 15 unique rule_ids below because each one is a documented design decision
# or a known cfn-nag rule limitation. After applying this deny-list,
# `cfn_nag` reports Failures: 0 and Warnings: 0 against template.yaml.
#
# Classification summary (Task 14.12 baseline scan):
#   - Failures  : 1 unique rule_id (F78)
#   - Warnings  : 14 unique rule_ids (W10, W11, W28, W35, W47, W48, W51,
#                 W58, W59, W64, W68, W84, W89, W92)
#   - True Positive corrections to template.yaml : 0 (zero)
#   - False Positive suppressions in this file   : 15
#
# Style: each entry's `reason` field follows the same one-line format as the
# four entries in infrastructure/.cfnlintrc:
#     "<rule_id> -- <one-line summary> -- <design source>".
# Detailed prose lives in the YAML comments above each entry, matching the
# file-comment style of .cfnlintrc.
#
# Tracking: docs/notes/_progress.md "Task 14.12" entry (Session 17).
```

以下、各 rule_id ごとに「YAML コメント逐語転載 + reason 行 + 補足解説」を提示する。

---

### 3.1 F78 — Cognito User Pool MfaConfiguration OFF

**rule_id 分類**：**Failures**（cfn-nag は `F` プレフィックスを Failure 扱い）

**YAML コメント逐語転載**：

```text
# F78 -- CognitoUserPool MfaConfiguration OFF
#
# design.md L845 explicitly records `MFA: OFF (初期構築)` for the User
# Pool. The mitigation stack is:
#   - 12-char password policy with upper / lower / digit / symbol
#     requirement (design.md L847, tasks.md L334)
#   - Administrator-group-only API authorization (design.md L197)
#   - Account lockout via LockoutTable + PreAuthentication Lambda
#     (design.md L227-228, Requirement 1.6)
# MFA activation is a Phase 14/15 production-readiness decision and is
# non-breaking (change MfaConfiguration to OPTIONAL or ON without data
# migration).
```

**reason 行**：

```text
F78 -- MfaConfiguration OFF is initial-build design -- design.md L845 "MFA OFF (初期構築)"; mitigated by 12-char password policy + Administrator group + LockoutTable; MFA activation deferred to Phase 14/15
```

**補足解説**：cfn-nag 唯一の Failures カテゴリ。design.md L845 で「MFA OFF (初期構築)」と明示されているため設計仕様。代替の認証強化として 12 文字パスワードポリシー + Administrator グループ限定認可 + LockoutTable の 3 重防御を取っており、MFA 有効化は本番運用フェーズ（Phase 14/15）で非破壊的に切替可能。

---

### 3.2 W10 — CloudFront Distribution access logging disabled

**YAML コメント逐語転載**：

```text
# W10 -- CloudFront Distribution access logging disabled
#
# SpaDistribution serves a low-traffic admin-only SPA. Per-request audit
# is covered server-side by:
#   - API Gateway access log (ApiGwAccessLogGroup) and execution log
#     (ApiGwExecutionLogGroup) — Phase 5.1
#   - AuditLogGroup populated by Lambda AuditLogWriter pattern —
#     Requirement 16.1-16.6 (Phase 16 Observability)
# CloudFront access logging would duplicate this audit trail and incur
# S3 storage cost without additional security value for admin-only
# traffic patterns.
```

**reason 行**：

```text
W10 -- CloudFront access log disabled -- admin-only low-traffic SPA; server-side audit via API Gateway access log + AuditLogGroup (Req 16.1-16.6) is the audit-of-record
```

**補足解説**：CloudFront access log は admin-only 低トラフィック SPA で重複ログとなる。API Gateway access log と AuditLogGroup（Req 16.1-16.6 = Phase 16 Observability）でサーバサイド監査が完結しているため不要。

---

### 3.3 W11 — IAM role allows "\*" resource

**YAML コメント逐語転載**：

```text
# W11 -- IAM role allows "*" resource
#
# Three roles legitimately require Resource: "*", and each location has
# an inline comment in template.yaml explaining why:
#
# (a) TranscribeStarterFnExecutionRole / TranscribeStartJob (L2200-04)
#     The transcribe:StartTranscriptionJob and
#     transcribe:GetTranscriptionJob actions do not support
#     resource-level permissions per the AWS IAM Service Authorization
#     Reference for Amazon Transcribe.
#
# (b) CycleFinalizerFnExecutionRole / CloudWatchPutMetric (L2478-83)
#     AWS docs explicitly state cloudwatch:PutMetricData does not
#     support Resource ARN scoping; namespace scoping is enforced by
#     the request body via the CLOUDWATCH_NAMESPACE env var.
#
# (c) CycleStateMachineExecutionRole / CloudWatchLogsForSfn (L3333-44)
#     The SFN CloudWatch Logs service-linked actions
#     (CreateLogDelivery / GetLogDelivery / UpdateLogDelivery /
#     DeleteLogDelivery / ListLogDeliveries / PutResourcePolicy /
#     DescribeResourcePolicies / DescribeLogGroups) require Resource
#     "*" per the AWS Step Functions logging-configuration docs; this
#     mirrors the AWS-managed policy
#     "AWSStepFunctionsConsoleFullAccess" wildcard pattern.
```

**reason 行**：

```text
W11 -- IAM "*" resource is AWS API requirement -- 3 occurrences (Transcribe StartTranscriptionJob, CloudWatch PutMetricData, SFN CloudWatch Logs service-linked); see inline comments at each Policy block
```

**補足解説**：3 箇所すべて AWS Service Authorization Reference 上で Resource ARN scoping をサポートしない API 要件のため不可避。template.yaml の各 Policy ブロックに inline コメントで「なぜ `*` か」を明示し、cfn-nag rule 限界として suppress。

---

### 3.4 W28 — Explicit resource name disallows replacement updates

**YAML コメント逐語転載**：

```text
# W28 -- Resource found with explicit name disallows replacement updates
#
# 38 resources carry explicit names (DynamoDB tables, IAM roles,
# managed policies, CloudWatch alarms, Lambda functions, SNS topic,
# SFN state machine, etc.). Predictable names are required because:
#   - DynamoDB table names appear in Lambda env vars and SFN
#     DefinitionSubstitutions (ResponseTableName placeholder, etc.).
#   - IAM role names are referenced by forward-named ARNs in cross-
#     resource policies (e.g. SfnStopExecution policy in
#     CycleFinalizerFnExecutionRole references the SFN execution ARN
#     before the SFN resource itself is declared).
#   - SNS topic / SFN state-machine names follow the
#     `safety-confirmation-<role>-${EnvironmentName}` convention so
#     Lambda env vars can be assembled with !Sub at template-parse
#     time without circular Ref dependencies (template.yaml comments
#     at OperatorTopicPublish, EventRuleDelete, etc.).
# The CloudFormation replacement-update constraint is accepted; per-
# environment splits use the EnvironmentName Parameter to avoid name
# collisions across dev/stg/prod accounts.
```

**reason 行**：

```text
W28 -- Explicit resource names required by !Sub-built ARN pattern -- 38 resources need predictable names for forward-named ARNs / Lambda env vars / SFN DefinitionSubstitutions; env split via EnvironmentName Parameter
```

**補足解説**：38 リソースが `!Sub` 経由で ARN を組み立てる必要があり、CFn 自動採番では順序依存（forward-named ARNs）が解決できない。CloudFormation replacement-update 制約は承知の上で採用、環境衝突は `EnvironmentName` Parameter で吸収。

---

### 3.5 W35 — S3 Bucket access logging disabled

**YAML コメント逐語転載**：

```text
# W35 -- S3 Bucket access logging disabled
#
# RecordingsBucket / TranscriptsBucket / SpaBucket all have
# PublicAccessBlockConfiguration with the four flags set to `true`
# (BlockPublicAcls / BlockPublicPolicy / IgnorePublicAcls /
# RestrictPublicBuckets). External access is therefore impossible.
# Object-level access audit, when needed, is covered by CloudTrail
# S3 Data Events (a Phase 16 Observability follow-up). S3 server
# access logging would duplicate that audit trail and incur storage
# cost without additional security value.
```

**reason 行**：

```text
W35 -- S3 access log disabled -- PublicAccessBlock all-true on all 3 buckets blocks external access; CloudTrail S3 Data Events (Phase 16) covers object-level audit when needed
```

**補足解説**：3 S3 bucket すべてが PublicAccessBlock 4 フラグ all-true で外部アクセス不可。Object-level audit が必要な場合は CloudTrail S3 Data Events で代替（Phase 16）、S3 server access log は重複ログとなる。

---

### 3.6 W47 — SNS Topic missing KmsMasterKeyId

**YAML コメント逐語転載**：

```text
# W47 -- SNS Topic missing KmsMasterKeyId
#
# OperatorTopic carries operator notifications only (SLA warnings,
# Cycle TIMEOUT messages — design.md L1518-1539 Operator notification
# design). No PII flows through this topic; the message body is
# cycle metadata (cycleId, status, counts). AWS-managed default
# encryption-in-transit (HTTPS) is sufficient. KMS CMK is
# intentionally scoped to resources that store PII at rest (DynamoDB
# tables, S3 buckets, Transcribe output) per the KMS scope decision
# mapped in design.md L1445 ("15.1, 15.6 ... KMS CMK + IAM 最小権限").
```

**reason 行**：

```text
W47 -- OperatorTopic CMK skipped -- operator notifications carry no PII (cycleId/status/counts only); CMK scope intentionally limited to PII-bearing rest stores (DDB / S3 / Transcript) per design.md L1445
```

**補足解説**：OperatorTopic は SLA 警告 / Cycle TIMEOUT 通知（cycleId / status / counts のみ）の運用通知用で PII を含まない。KMS CMK は PII at rest（DDB / S3 / Transcript）のみに限定する設計（design.md L1445）。

---

### 3.7 W48 — SQS Queue missing KmsMasterKeyId

**YAML コメント逐語転載**：

```text
# W48 -- SQS Queue missing KmsMasterKeyId
#
# RecordingMetadataWriterDLQ receives Lambda async-invoke failure
# payloads — S3 EventBridge events containing object keys
# (s3://recordings/<cycleId>/<employeeIdSeq>/<file>.wav). The actual
# recording media stays SSE-KMS in RecordingsBucket; only the key
# string lands in the DLQ. AWS-managed SQS default encryption is
# sufficient for this metadata payload.
```

**reason 行**：

```text
W48 -- DLQ CMK skipped -- payload is EventBridge metadata (object keys), no PII; real recording media stays SSE-KMS in RecordingsBucket
```

**補足解説**：DLQ payload は EventBridge メタデータ（S3 オブジェクトキー文字列のみ）。実録音媒体は RecordingsBucket 側で SSE-KMS 維持、DLQ には PII が落ちない設計。

---

### 3.8 W51 — S3 bucket should likely have a bucket policy

**YAML コメント逐語転載**：

```text
# W51 -- S3 bucket should likely have a bucket policy
#
# RecordingsBucket / TranscriptsBucket: explicit AWS::S3::BucketPolicy
# resources are intentionally omitted. Access control is enforced by
# two layers:
#   1. PublicAccessBlockConfiguration all-true (no public principal
#      can reach the buckets).
#   2. IAM role scoping — only specific Lambda execution roles carry
#      s3:GetObject / s3:PutObject for these buckets:
#        - TranscribeStarterFnExecutionRole.RecordingsBucketRead
#          (s3:GetObject on RecordingsBucket/*)
#        - TranscribeStarterFnExecutionRole.TranscriptsBucketWrite
#          (s3:PutObject + s3:PutObjectTagging on TranscriptsBucket/*)
#        - KeywordMatcherFnExecutionRole.TranscriptsBucketRead, etc.
#     Every other principal hits the IAM implicit deny.
# Phase 2.10 / 2.11 Done When ("認可外プリンシパルからの GetObject が
# AccessDenied") is satisfied by these two layers. The legacy
# template.yaml comment at L899-901 ("BucketPolicy is added in Phase 6
# once Lambda execution roles exist") references an early plan note
# that was not carried through to the final design.
# NOTE: SpaBucket DOES have an explicit AWS::S3::BucketPolicy
# (SpaBucketPolicy at L4197) because CloudFront OAC requires an
# explicit allow-from-OAC statement; cfn-nag correctly accepts that
# one and only flags the Recordings/Transcripts pair.
```

**reason 行**：

```text
W51 -- Recordings/Transcripts BucketPolicy omitted -- PublicAccessBlock all-true + IAM Role-scoped GetObject/PutObject enforce AccessDenied for non-authorized principals; Phase 2.10/2.11 Done When satisfied without explicit BucketPolicy
```

**補足解説**：Recordings / Transcripts bucket は BucketPolicy なしでも (1) PublicAccessBlock all-true + (2) IAM Role scope の二層で AccessDenied 強制。Phase 2.10 / 2.11 Done When（認可外プリンシパルからの GetObject が AccessDenied）が充足済。SpaBucket は CloudFront OAC のために BucketPolicy が必要であり、これは cfn-nag が正しく通すため対象外。

**副次発見トリガ**：YAML コメント内に「The legacy template.yaml comment at L899-901 ... references an early plan note that was not carried through to the final design」と記録されている。これは template.yaml 本体のコメントに「BucketPolicy is added in Phase 6 once Lambda execution roles exist」というレガシー残骸が存在することを示しており、§6 で整地候補として別タスク化候補。

---

### 3.9 W58 — Lambda functions require CloudWatch Logs permission

**YAML コメント逐語転載**：

```text
# W58 -- Lambda functions require CloudWatch Logs permission
#
# All 19 Lambda functions attach LambdaBaseLogsManagedPolicy via the
# ManagedPolicyArns list on their execution roles. That managed
# policy grants logs:CreateLogGroup, logs:CreateLogStream, and
# logs:PutLogEvents on the dedicated Lambda LogGroups (each Lambda
# has a paired AWS::Logs::LogGroup created in Phase 12.1). cfn-nag
# W58 does not follow ManagedPolicyArns references — it only
# inspects Policies / inline policy documents — so it flags all 19
# roles as missing log permissions. This is a known limitation of
# the W58 rule and is acknowledged in cfn-nag GitHub issues.
```

**reason 行**：

```text
W58 -- cfn-nag rule limitation -- all 19 Lambda roles attach LambdaBaseLogsManagedPolicy via ManagedPolicyArns (logs:CreateLogGroup/CreateLogStream/PutLogEvents); W58 does not resolve ManagedPolicyArns refs
```

**補足解説**：cfn-nag W58 ルールが `Policies` / inline policy のみを inspect し `ManagedPolicyArns` 参照を辿らない既知の rule 限界。本プロジェクトは 19 Lambda Execution Role すべてに `LambdaBaseLogsManagedPolicy` を ManagedPolicyArns 経由で添付しており、logs 系 IAM 権限は確実に付与されている（cfn-nag GitHub issues でも本限界は認知済）。

---

### 3.10 W59 — ApiGateway Method AuthorizationType=NONE

**YAML コメント逐語転載**：

```text
# W59 -- ApiGateway Method AuthorizationType=NONE outside HttpMethod OPTIONS
#
# AuthRecordFailureMethod is the public endpoint
# `POST /auth/record-failure` that the SPA hits AFTER it detects a
# Cognito NotAuthorizedException (design.md L227, tasks.md L470-473).
# By definition the SPA has no valid Cognito ID/Access token at that
# moment, so requiring Cognito authorization on this endpoint would
# be a tautological dead end — the very failure being reported is
# the absence of a valid token.
# Brute-force abuse of this unauthenticated path is mitigated by
# API Gateway stage throttling configured at 10 req/sec/IP
# (tasks.md L471). Rate exceedance returns HTTP 429.
```

**reason 行**：

```text
W59 -- AuthRecordFailureMethod is the unauthenticated public failure-reporting endpoint -- design.md L227 / tasks.md L470-473; SPA has no valid Cognito token at the moment of POST; brute force throttled to 10 req/sec/IP
```

**補足解説**：`POST /auth/record-failure` は SPA が Cognito NotAuthorizedException を検出した直後に呼び出す **未認証専用エンドポイント**。認証失敗を報告する経路に認証要求を課すと tautology になる。ブルートフォース耐性は API Gateway Stage Throttle（10 req/sec/IP）で確保（design.md L227 / tasks.md L470-473）。

---

### 3.11 W64 / W68 — ApiGateway Stage/Deployment missing UsagePlan

**YAML コメント逐語転載**：

```text
# W64 -- ApiGateway Stage missing UsagePlan
# W68 -- ApiGateway Deployment missing UsagePlan
#
# UsagePlan is the AWS construct for API-Key quota management. This
# system authorizes every API call via Cognito User Pool JWT (no API
# Keys exist), and rate-limit enforcement is implemented at the
# Stage level using ApiThrottleRate / ApiThrottleBurst Mappings
# entries (design.md L870-871, tasks.md L484, dev=50/100,
# stg=100/200, prod=100/200). Introducing a UsagePlan would add
# API-Key infrastructure that the design does not use.
```

**reason 行**（2 件、同根拠）：

```text
W64 -- UsagePlan unused -- Cognito JWT authorization (no API Keys); rate-limit via Stage ApiThrottleRate/Burst (design.md L870-871, tasks.md L484)
W68 -- UsagePlan unused -- same rationale as W64; this system has no API Keys, rate-limit is Stage-level throttling
```

**補足解説**：UsagePlan は API-Key 経由のクォータ管理用 AWS 構造体。本システムは Cognito User Pool JWT 認可で API-Key を使わないため、UsagePlan を導入するとデッドストック資源になる。rate-limit は Stage の ApiThrottleRate / ApiThrottleBurst（dev=50/100, stg=100/200, prod=100/200）で代替（design.md L870-871）。

---

### 3.12 W84 — CloudWatchLogs LogGroup missing KmsKeyId

**YAML コメント逐語転載**：

```text
# W84 -- CloudWatchLogs LogGroup missing KmsKeyId
#
# 23 LogGroups (19 Lambda log groups + CycleStateMachineLogGroup +
# ApiGwExecutionLogGroup + ApiGwAccessLogGroup + AuditLogGroup) rely
# on the AWS-managed CloudWatch Logs default encryption (service-
# managed key, automatic). KMS CMK is intentionally scoped to PII-
# bearing rest stores (DynamoDB tables, S3 buckets, Transcribe
# output). Log content is treated as masked at source: the
# `maskPhone` shared function (design.md L1446 "Observability ...
# maskPhone 関数") redacts phone numbers and other PII before any
# logger emits, so logs never carry raw PII to begin with.
```

**reason 行**：

```text
W84 -- LogGroup CMK skipped -- PII masked at source via shared maskPhone (design.md L1446); LogGroups rely on AWS-managed default encryption; CMK scope limited to PII-bearing rest stores
```

**補足解説**：23 LogGroup（Lambda 19 + SFN + ApiGw 2 + Audit）は AWS-managed default 暗号化に依存。PII は emit 前に `shared/observability/maskPhone` で除去済（design.md L1446）のため、LogGroup に PII が落ちない設計。

---

### 3.13 W89 — Lambda functions should be deployed inside a VPC

**YAML コメント逐語転載**：

```text
# W89 -- Lambda functions should be deployed inside a VPC
#
# The system architecture excludes VPC by design:
#   - design.md L194: "VPC は使用しない。"
#   - design.md L209: "ネットワーク | VPC 不使用"
# All AWS service integrations (API Gateway, DynamoDB, S3, Cognito,
# Connect, Transcribe, SFN, SNS, EventBridge) are public-endpoint
# managed services reached over the AWS backbone with TLS 1.2+
# (design.md L196). No private resources are touched, so VPC
# deployment would only add cold-start latency (ENI attachment) and
# NAT cost without security benefit.
```

**reason 行**：

```text
W89 -- VPC unused by design -- design.md L194/L209 "VPC 不使用"; all deps are AWS managed services reached via public endpoints over TLS 1.2+
```

**補足解説**：design.md L194「VPC は使用しない。」/ L209「VPC 不使用」と明示。全 AWS 依存先がマネージドサービスのパブリックエンドポイントで TLS 1.2+ 経由のため、VPC 配置は cold-start ENI attachment レイテンシ + NAT 費用増だけで security 価値なし。

---

### 3.14 W92 — Lambda functions should define ReservedConcurrentExecutions

**YAML コメント逐語転載**：

```text
# W92 -- Lambda functions should define ReservedConcurrentExecutions
#
# All 19 Lambdas run within the AWS default per-region concurrency
# pool (1000 concurrent executions). Throughput throttling is
# enforced upstream at two layers:
#   - SFN Map MaxConcurrency=10 caps outbound dispatch
#     (design.md L1515: "Connect 同時アクティブコール 10 が上限")
#   - API Gateway stage throttling caps inbound API calls
#     (50-100 req/sec depending on env, design.md L870-871)
# Reserving concurrency per function would partition the regional
# pool without adding back-pressure beyond what the upstream
# throttles already provide.
```

**reason 行**：

```text
W92 -- ReservedConcurrentExecutions unset by design -- SFN Map MaxConcurrency=10 + API Gateway stage throttle cap upstream rates (design.md L1515 / L870-871); per-function reservation adds no back-pressure
```

**補足解説**：19 Lambda はリージョン default プール（1000 concurrent）内で動作。throughput 制約は上流の SFN Map MaxConcurrency=10（Connect 同時 10 callup 制約、design.md L1515）+ API Gateway Stage Throttle（50-100 req/sec、design.md L870-871）で確保。per-function ReservedConcurrent は二重制約になるだけで back-pressure を増やさない。

---

### 3.15 15 unique rule_id 統合サマリ

| #   | rule_id | 種別     | 設計根拠カテゴリ                | 1 行要約                                                        |
| --- | ------- | -------- | ------------------------------- | --------------------------------------------------------------- |
| 1   | F78     | Failures | Cognito MFA 設計判断            | 初期構築 MFA OFF + 12 文字パスワード + Lockout 三重防御         |
| 2   | W10     | Warnings | CloudFront 監査の重複回避       | API GW access log + AuditLogGroup でサーバ側監査完結            |
| 3   | W11     | Warnings | IAM API 要件（Resource 不可避） | 3 箇所が AWS 公式 service docs で `*` 必須                      |
| 4   | W28     | Warnings | リソース名予測可能性            | 38 リソースが `!Sub` 経由 ARN 構築のため必要                    |
| 5   | W35     | Warnings | S3 監査の重複回避               | PublicAccessBlock all-true + CloudTrail Data Events 経路        |
| 6   | W47     | Warnings | SNS の PII 非含有               | OperatorTopic は cycleId/status/counts のみ                     |
| 7   | W48     | Warnings | SQS の PII 非含有               | DLQ payload は EventBridge メタデータのみ                       |
| 8   | W51     | Warnings | S3 BucketPolicy 不要設計        | PublicAccessBlock + IAM Role scope で AccessDenied 二層         |
| 9   | W58     | Warnings | cfn-nag rule 限界               | ManagedPolicyArns 経由添付を rule が辿らない                    |
| 10  | W59     | Warnings | 認証失敗報告 endpoint           | SPA が token 無し状態で叩く専用 endpoint、Stage Throttle で防御 |
| 11  | W64     | Warnings | UsagePlan 不使用設計            | Cognito JWT 認証で API-Key 不存在、Stage Throttle で代替        |
| 12  | W68     | Warnings | UsagePlan 不使用設計            | W64 と同根拠                                                    |
| 13  | W84     | Warnings | LogGroup CMK 不要設計           | maskPhone で PII 除去済、AWS-managed 既定暗号化で十分           |
| 14  | W89     | Warnings | VPC 不使用設計                  | design.md 明示、全 dep が public endpoint                       |
| 15  | W92     | Warnings | ReservedConcurrent 不使用設計   | SFN MaxConcurrency=10 + Stage Throttle で上流制約済             |

---

## 4. 実行手順

### 4.1 Docker image pull

```powershell
# 初回のみ
docker pull stelligent/cfn_nag:latest
```

### 4.2 deny-list 適用スキャン（本タスク採用コマンド）

```powershell
# リポジトリ root から
docker run --rm -v "${PWD}:/workspace" stelligent/cfn_nag --deny-list-path /workspace/infrastructure/.cfn_nag_rules.yml /workspace/infrastructure/template.yaml
```

**期待出力**：

```text
Failures count: 0
Warnings count: 0
```

### 4.3 baseline スキャン（suppress 抜き、参考用）

```powershell
docker run --rm -v "${PWD}:/workspace" stelligent/cfn_nag /workspace/infrastructure/template.yaml
```

**baseline 結果**（Session 17 計測）：

```text
Failures count: 1     (unique rule_id : F78)
Warnings count: 133   (unique rule_id : 14 種 = W10/W11/W28/W35/W47/W48/W51/W58/W59/W64/W68/W84/W89/W92)
```

---

## 5. baseline スキャン結果と suppress 後の差分

| 段階                        | Failures      | Warnings        | unique rule_id   |
| --------------------------- | ------------- | --------------- | ---------------- |
| baseline（suppress 適用前） | **1**         | **133**         | 1 + 14 = 15      |
| `.cfn_nag_rules.yml` 適用後 | **0**         | **0**           | 0                |
| 差分（解消件数）            | 1 件（−100%） | 133 件（−100%） | 15 件全 suppress |

**True Positive 件数**：**0**（template.yaml に対する実コード修正は本タスクで一切行わなかった）

**False Positive 件数**：**15**（全 unique rule_id を `.cfn_nag_rules.yml` で suppress）

各 unique rule_id × suppress 件数の内訳（baseline 133 Warnings の内訳）は `_progress.md` 申し送りには展開されていないが、本タスクの完了所感としては unique rule_id 単位での全数把握で十分。

---

## 6. 副次発見（template.yaml レガシーコメント整地候補）

### 6.1 発見内容

`.cfn_nag_rules.yml` W51 entry の YAML コメント内（§3.8 参照）で発見された：

```text
The legacy template.yaml comment at L899-901
("BucketPolicy is added in Phase 6 once Lambda execution roles exist")
references an early plan note that was not carried through to the final design.
```

**該当箇所**：

- `infrastructure/template.yaml` **L899-901**：Recordings バケット周辺に「BucketPolicy is added in Phase 6 once Lambda execution roles exist」というレガシーコメント
- `infrastructure/template.yaml` **L929**：Transcripts バケット周辺に同種のレガシーコメント

これらのコメントは初期計画段階の "Phase 6 で BucketPolicy 追加予定" を記録したものだが、最終設計では「PublicAccessBlock + IAM Role scope の二層でアクセス制御、BucketPolicy なし」に方針変更されており、コメントだけが残骸として残った状態。

### 6.2 影響範囲

- **機能影響**：なし（コメントのみ、CFn ロジックには影響しない）
- **保守性**：将来のレビュー時に「Phase 6 で何か追加するはずだったのか？」と混乱を招くノイズ

### 6.3 別タスク化候補

**ID**：**15.18**（軽量、Markdown / YAML 編集のみ、Connect 非依存）

**起票状態**：未起票（`_progress.md` Session 17 末申し送りで「次セッション以降の軽量整地候補」として認知済）

**Done When**：

- template.yaml L899-901 / L929 のレガシーコメント削除
- `.cfn_nag_rules.yml` W51 entry の「legacy plan note」言及部分も同時更新（解消したことの記録）

---

## 7. 関連ファイル一覧

### 7.1 本タスク 14.12 で作成 / 修正

- `infrastructure/.cfn_nag_rules.yml`（新規、約 230 行、15 unique rule_id を `RulesToSuppress` リストに登録）
- `.kiro/specs/safety-confirmation-system/tasks.md`（14.12 を `[x]` 化、orchestrator 側で str_replace 直接編集）
- `docs/notes/_progress.md`（Session 17 末セクション追加）

### 7.2 本ノート 15.25 で作成

- `docs/notes/14-12-cfn-nag.md`（本ファイル、新規）

### 7.3 関連既存ノート（DRY 原則、引用統合）

- [`docs/notes/14-10-cfn-smoke.md`](./14-10-cfn-smoke.md) — B5 経緯（14.10 で cfn-nag skip → 14.12 別タスク化）
- [`docs/notes/15-6a-non-connect-acceptance.md`](./15-6a-non-connect-acceptance.md) — Connect 非依存 Acceptance Criteria 踏破レポート（§3.7 / §3.8 で本ノートが参照される）
- [`docs/notes/_progress.md`](./_progress.md) — Session 17 末申し送り

### 7.4 参照した spec / 設計

- [`infrastructure/.cfn_nag_rules.yml`](../../infrastructure/.cfn_nag_rules.yml) — 15 suppress rule_id + YAML コメント（本ノートの「真の仕様」ソース）
- [`infrastructure/template.yaml`](../../infrastructure/template.yaml) — CFn テンプレート（L899-901 / L929 レガシーコメント候補箇所）
- [`.kiro/specs/safety-confirmation-system/requirements.md`](../../.kiro/specs/safety-confirmation-system/requirements.md) — Requirement 17（IaC）/ NFR3
- [`.kiro/specs/safety-confirmation-system/design.md`](../../.kiro/specs/safety-confirmation-system/design.md) — Testing Strategy / スモークテスト（L1232）/ Operator notification design（L1518-1539）/ Observability maskPhone（L1446）/ KMS scope（L1445）/ ApiThrottle Mappings（L870-871）/ SFN MaxConcurrency=10（L1515）/ VPC 不使用（L194 / L209）/ MFA OFF（L845）/ Password Policy（L847）
- [`.kiro/specs/safety-confirmation-system/tasks.md`](../../.kiro/specs/safety-confirmation-system/tasks.md) — タスク 14.12（L1202-1211）

---

## 8. 所感

`14.12` は `14.10` の Done When (2)「`cfn-nag` で major issue 0」を独立タスク化した補完タスクであり、A 採用方針（実装を真の仕様とし、スキップ事項を別タスク起票で消化）の典型例。本タスクは IaC コード（template.yaml）への実修正をゼロに保ちつつ、cfn-nag が指摘する全 15 unique rule_id を「設計判断 or rule 限界」として正当化し、Failures 0 / Warnings 0 を達成した。

採用した Docker image `stelligent/cfn_nag:latest` 方式は、Ruby ランタイム不在の Windows 開発環境で gem install の手戻りを排除する判断であり、本判断の根拠は「Docker Desktop 既存環境 + 1 行 docker run で完結 + CI 統合容易性」の 3 点に集約される。Session 16 検証で発見された CLI API 差異（`cfn_nag` vs `cfn_nag_scan --input-path`）は image 採用時の正しいコマンドを `--deny-list-path` 経由で確定させ、tasks.md L1206 に経緯記録済。

`.cfn_nag_rules.yml` の各 entry に `reason` フィールド（1 行）+ YAML コメント（詳細散文）の二層構造を採用したのは、`infrastructure/.cfnlintrc` の文書化スタイル（4 rule_id × 1 行 reason + 詳細コメント）と一貫性を持たせる目的。これにより `.cfnlintrc` ↔ `.cfn_nag_rules.yml` が「同じ流儀の suppress 文書」として並列に保守可能になり、IaC 品質保証の二大ツールが対称形に整理される。

副次発見の template.yaml L899-901 / L929 レガシーコメント整地は IaC 機能には影響しない軽量整地だが、`.cfn_nag_rules.yml` W51 entry でメタ参照されているため、将来 15.18 として消化する際は「.cfn_nag_rules.yml W51 entry のメタ言及部分も同時に更新」が Done When として連動する。第 17 原則（対称性推論）に従い、template.yaml 側だけを修正して `.cfn_nag_rules.yml` を放置すると、コメント間で矛盾が発生するため、双方向の整合が必要。
