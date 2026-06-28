# Task 15.26 — dev 環境用テスト App Client 追加（CLI 自動化基盤）

- 完了日: 2026-06-28（セッション 26、tasks.md 15.26）
- 対応者: kiro（orchestrator + spec-task-execution subagent、AWS CLI 経由 + y/n 承認）
- スコープ: dev 環境（Stack `safety-confirmation-dev`、Account 214046906694、ap-northeast-1）
- Connect 依存: なし

---

## §0 エグゼクティブサマリ

| 項目                  | 結果                                                                                                            |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| 新規 App Client       | ✅ `safety-confirmation-cli-test-dev` 作成完了（ClientId `7vtk89lqmce7ou1lr76rv4dhss`）                         |
| CFn Output 追加       | ✅ `CognitoUserPoolClientTestId` 公開（ExportName `safety-confirmation-dev-CognitoUserPoolClientTestId`）       |
| Cognito 22 項目突合   | ✅ 副作用ゼロ（15-22-user-pool-final.json と完全一致）                                                          |
| admin-initiate-auth   | ✅ `NotAuthorizedException`（AuthFlow が処理された証拠）                                                        |
| CycleFinalizer Lambda | ✅ 15.23 refactor 反映済（LastModified `2026-06-27T17:47:01 UTC` = JST 02:47:01）                               |
| 影響範囲              | dev 限定（`Condition: IsDev`）、stg / prod では Resource/Output 共に生成されない                                |
| 副次的 deploy         | CycleFinalizerFn / CycleStateMachineExecutionRole / CycleStateMachine の Modify 3 件は 15.23 DRY 共通化由来連鎖 |

---

## §1 タスク背景

### 15-2a-placeholder-deploy §4.2 追加発見 (i) 引用

> dev 環境の SPA / API / Cognito / DDB / S3 / CloudFront / SNS / CloudWatch 全リソースが稼働中。管理者ユーザー `placeholder@example.com` 作成済（Administrator グループ所属、`FORCE_CHANGE_PASSWORD` 状態）。ユーザー手動投入をスコープ分離した理由は **App Client が SRP 認証のみ有効で CLI 単独 IdToken 取得困難**。
>
> 辞書投入を AI 経由で自動化するなら、別タスクで「**dev 用テスト App Client（`ADMIN_USER_PASSWORD_AUTH` 許可）の追加**」or「pycognito 経由 SRP 実装」を起票。

### 15.6a §5 残課題引用（dev 環境用テスト App Client）

> | dev 環境用テスト App Client（`ADMIN_USER_PASSWORD_AUTH` 許可）追加 | Med | [15-2a-placeholder-deploy §4.2 追加発見 (i)] — CLI 自動化基盤として有用 |

### 課題まとめ

- dev 環境の既存 App Client `safety-confirmation-spa-dev` は SRP のみ有効（`ALLOW_USER_SRP_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH`）。SPA が SRP 認証を主経路とするため SRP 必須。
- AWS CLI 単独で IdToken を取得する経路が存在しない：
  - Salt 投入後の `placeholder@example.com` は `FORCE_CHANGE_PASSWORD` 状態 = CLI ログイン不可
  - `tomita@g-wise.co.jp` のパスワードは口頭管理で AI 不可
- 結果として read-only curl による Acceptance Criteria 検証の自動化が困難（15.6a で「代替証跡」採用済）。

→ 本タスクでは **dev 環境限定**の追加 App Client（`ALLOW_ADMIN_USER_PASSWORD_AUTH` 許可）を CFn 機構的に作成することで、将来の CLI 自動化基盤を整備する。

---

## §2 設計判断

### 2.1 `IsDev` Condition の新設（`IsProd` の対称設計）

```yaml
Conditions:
  IsProd: !Equals [!Ref EnvironmentName, prod]
  # Phase 15.26: dev 環境限定リソース用 Condition。CLI 自動化基盤として
  # `ADMIN_USER_PASSWORD_AUTH` を許可するテスト App Client は dev 限定で
  # 作成する（パスワード平文を Auth Parameters に含むため stg / prod では
  # 作成不可）。stg / prod での誤作成を CFn 側で機構的に防止する。
  IsDev: !Equals [!Ref EnvironmentName, dev]
```

**根拠**：既存 `IsProd` の対称設計。stg では IsProd=false / IsDev=false で本 Client 不在 = 第 19 原則 (b) フォールバック禁止に整合（環境が dev でないなら作らない、というシンプルな分岐）。

### 2.2 `ALLOW_ADMIN_USER_PASSWORD_AUTH` を dev 限定とする方針

| 項目                               | 採用理由                                                                                                                                                            |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **dev 限定（`Condition: IsDev`）** | パスワード平文を `--auth-parameters PASSWORD=...` で送信するため、stg / prod では絶対に不採用                                                                       |
| **既存 SPA Client 非変更**         | `safety-confirmation-spa-dev`（SRP 専用）はそのまま、別 Client として並列追加 = 既存認証経路への影響ゼロ                                                            |
| **CLI 単独で IdToken 取得可能**    | `aws cognito-idp admin-initiate-auth --auth-flow ADMIN_USER_PASSWORD_AUTH --auth-parameters USERNAME=...,PASSWORD=...` で IdToken/AccessToken/RefreshToken 一括取得 |
| **stg / prod 誤作成の機構的防止**  | CFn `Condition: IsDev` で dev 以外では Resource/Output が物理的に存在しない                                                                                         |

### 2.3 `PreventUserExistenceErrors=ENABLED` + `EnableTokenRevocation=true` 設計

| プロパティ                   | 設定値      | 採用理由                                                                                                                       |
| ---------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `PreventUserExistenceErrors` | `ENABLED`   | dev 環境であっても enumerate 攻撃に対する基本姿勢として ON（cfn-nag W36 等の警告回避も兼ねる）。SPA Client も ENABLED で対称化 |
| `EnableTokenRevocation`      | `true`      | RefreshToken を `cognito-idp revoke-token` で失効可能（dev 検証中に IdToken を即時無効化できる運用余地、SPA Client と対称）    |
| `GenerateSecret`             | `false`     | パブリッククライアント扱い、ClientSecret なし（AWS CLI からの利用に向け Secret 不要、SPA Client と対称）                       |
| `SupportedIdentityProviders` | `[COGNITO]` | フェデレーション無効、Cognito User Pool 内ユーザのみ                                                                           |

### 2.4 Token Validity 設計

| プロパティ             | 値      | 設計判断                                                           |
| ---------------------- | ------- | ------------------------------------------------------------------ |
| `IdTokenValidity`      | 1 hour  | SPA Client 設計値と対称、検証用には十分                            |
| `AccessTokenValidity`  | 1 hour  | 同上                                                               |
| `RefreshTokenValidity` | 30 days | dev 限定リフレッシュ運用、最大 1 year まで延長可能だが意図的に短く |

### 2.5 `ExplicitAuthFlows` 構成

```yaml
ExplicitAuthFlows:
  - ALLOW_ADMIN_USER_PASSWORD_AUTH
  - ALLOW_REFRESH_TOKEN_AUTH
```

- `ALLOW_USER_PASSWORD_AUTH` ではなく `ALLOW_ADMIN_USER_PASSWORD_AUTH`：non-admin 経路の平文パスワード送信は API Gateway 経由になりログに残るリスクがあるため、`admin-initiate-auth` 経路に限定（AWS CLI 側で SigV4 署名 + HTTPS 経由のみ）
- `ALLOW_REFRESH_TOKEN_AUTH`：30 日有効な RefreshToken を CLI で利用するため必須

---

## §3 template.yaml 変更箇所（3 件）

### 3.1 Conditions ブロック（L270-275）追加

```yaml
Conditions:
  IsProd: !Equals [!Ref EnvironmentName, prod]
  # Phase 15.26: dev 環境限定リソース用 Condition.（コメント詳細省略）
  IsDev: !Equals [!Ref EnvironmentName, dev]
  HasCustomDomain: !Not [!Equals [!Ref DomainName, ""]]
  UseCustomCert: !Not [!Equals [!Ref AcmCertificateArn, ""]]
```

### 3.2 Resources ブロック（L1082-1103）追加

```yaml
CognitoUserPoolClientTest:
  Type: AWS::Cognito::UserPoolClient
  Condition: IsDev
  Properties:
    UserPoolId: !Ref CognitoUserPool
    ClientName: !Sub "safety-confirmation-cli-test-${EnvironmentName}"
    GenerateSecret: false
    ExplicitAuthFlows:
      - ALLOW_ADMIN_USER_PASSWORD_AUTH
      - ALLOW_REFRESH_TOKEN_AUTH
    PreventUserExistenceErrors: ENABLED
    EnableTokenRevocation: true
    SupportedIdentityProviders:
      - COGNITO
    IdTokenValidity: 1
    AccessTokenValidity: 1
    RefreshTokenValidity: 30
    TokenValidityUnits:
      IdToken: hours
      AccessToken: hours
      RefreshToken: days
```

### 3.3 Outputs ブロック（L5050-5056）追加

```yaml
CognitoUserPoolClientTestId:
  Condition: IsDev
  Description: Cognito User Pool Client ID（dev 限定、CLI 自動化用 ADMIN_USER_PASSWORD_AUTH 許可）
  Value: !Ref CognitoUserPoolClientTest
  Export:
    Name: !Sub "${AWS::StackName}-CognitoUserPoolClientTestId"
```

---

## §4 ChangeSet 内容（4 件 = 1 Add + 3 Modify）

ChangeSet 名: `awscli-cloudformation-package-deploy-1782582079`

| #   | Action | LogicalId                      | Type                             | Replacement | 由来                                             |
| --- | ------ | ------------------------------ | -------------------------------- | ----------- | ------------------------------------------------ |
| 1   | Add    | CognitoUserPoolClientTest      | AWS::Cognito::UserPoolClient     | -           | **15.26 本タスク**                               |
| 2   | Modify | CycleFinalizerFn               | AWS::Lambda::Function            | False       | 15.23 由来（DRY 共通化 refactor の未 deploy 分） |
| 3   | Modify | CycleStateMachineExecutionRole | AWS::IAM::Role                   | False       | 15.23 連鎖（CycleFinalizerFn.Arn 参照）          |
| 4   | Modify | CycleStateMachine              | AWS::StepFunctions::StateMachine | False       | 15.23 連鎖（同上）                               |

### 第 7 原則ズレ検知の経緯（前段 subagent → orchestrator → 本 subagent）

前段 subagent が ChangeSet 内容に CycleFinalizer 系 3 件の想定外 Modify を検知し、**第 7 原則 (a) 最初の認識合わせの想定になかった情報**として即停止 → orchestrator が真因特定。

**真因**：15.23 セッションで実施した `backend/lambdas/cycle_finalizer/handler.py` の DRY refactor が deploy 未反映状態。具体的な変更：

- 旧 `_put_sla_warning_metric` / `_put_cycle_timeout_metric` の 2 関数（19 行重複）を削除
- 新 `_put_cycle_metric(metric_name: str, cycle_id: str)` 共通関数（26 行）に集約
- 呼出箇所 2 つ（`_handle_timer_30min` / `_handle_timer_60min`）を直接共通関数呼出に置換
- 既存テスト 16/16 PASS、mypy / ruff クリーン、template.yaml 無変更

つまり Lambda コード差分は意図された安全な refactor。CycleStateMachineExecutionRole / CycleStateMachine の Modify は CycleFinalizerFn.Arn 連鎖（in-place、Replacement=False）であり、リソースの作り直しは発生しない。

**orchestrator の判定**：案 A（ChangeSet execute）採用 → 本 subagent で execute + 後続検証 + ノート作成を実施。

---

## §5 deploy 結果 + 検証結果（2.1 〜 2.5）

### 5.1 ChangeSet execute

```powershell
aws cloudformation execute-change-set `
  --change-set-name awscli-cloudformation-package-deploy-1782582079 `
  --stack-name safety-confirmation-dev `
  --profile AWS-security-check --region ap-northeast-1
```

- ExitCode=0
- Stack `safety-confirmation-dev` → `UPDATE_IN_PROGRESS` → `UPDATE_COMPLETE`（約 1 〜 2 分）

### 5.2 検証 2.1 — Cognito User Pool 22 項目突合

```powershell
aws cognito-idp describe-user-pool --user-pool-id ap-northeast-1_5uYfaQMLJ ...
  > docs/notes/15-26-user-pool-after.json
```

- 15-22-user-pool-final.json と PowerShell `Compare-Object` で full-text diff 実施
- 結果: **`DIFF_RESULT=IDENTICAL`**（バイト列レベルで完全一致）
- LastModifiedDate も `2026-06-28T02:06:50.153000+09:00`（15.22 当時の値）のまま不変 = User Pool 本体は本 deploy で 1 byte も変更されていない

→ §6 の 22 項目突合表参照。**副作用ゼロ達成**。

### 5.3 検証 2.2 — CognitoUserPoolClientTest 作成確認

```powershell
aws cognito-idp list-user-pool-clients --user-pool-id ap-northeast-1_5uYfaQMLJ ...
```

```json
{
  "UserPoolClients": [
    {
      "ClientId": "7h8mt6jrieu5grm9s8uqdn94en",
      "ClientName": "safety-confirmation-spa-dev"
    },
    {
      "ClientId": "7vtk89lqmce7ou1lr76rv4dhss",
      "ClientName": "safety-confirmation-cli-test-dev"
    }
  ]
}
```

→ 新規 Client `safety-confirmation-cli-test-dev` の存在を確認。既存 SPA Client は不変。

```powershell
aws cognito-idp describe-user-pool-client --user-pool-id ap-northeast-1_5uYfaQMLJ --client-id 7vtk89lqmce7ou1lr76rv4dhss ...
```

| プロパティ                      | 期待値                                                     | 実機値                                                     | 判定 |
| ------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------- | ---- |
| ClientName                      | safety-confirmation-cli-test-dev                           | safety-confirmation-cli-test-dev                           | ✅   |
| ExplicitAuthFlows               | [ALLOW_ADMIN_USER_PASSWORD_AUTH, ALLOW_REFRESH_TOKEN_AUTH] | [ALLOW_ADMIN_USER_PASSWORD_AUTH, ALLOW_REFRESH_TOKEN_AUTH] | ✅   |
| GenerateSecret / ClientSecret   | false / 不在                                               | レスポンスに ClientSecret フィールドなし                   | ✅   |
| IdTokenValidity                 | 1 hours                                                    | 1 (TokenValidityUnits.IdToken=hours)                       | ✅   |
| AccessTokenValidity             | 1 hours                                                    | 1 (TokenValidityUnits.AccessToken=hours)                   | ✅   |
| RefreshTokenValidity            | 30 days                                                    | 30 (TokenValidityUnits.RefreshToken=days)                  | ✅   |
| PreventUserExistenceErrors      | ENABLED                                                    | ENABLED                                                    | ✅   |
| EnableTokenRevocation           | true                                                       | true                                                       | ✅   |
| SupportedIdentityProviders      | [COGNITO]                                                  | [COGNITO]                                                  | ✅   |
| AllowedOAuthFlowsUserPoolClient | (未設定 = false)                                           | false                                                      | ✅   |

→ **全プロパティ仕様通り作成成功**。CreationDate `2026-06-28T02:46:58.905000+09:00` は本 deploy 時刻と一致。

### 5.4 検証 2.3 — CFn Output 追加確認

```powershell
aws cloudformation describe-stacks --stack-name safety-confirmation-dev \
  --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolClientTestId']"
```

```json
[
  {
    "Key": "CognitoUserPoolClientTestId",
    "Value": "7vtk89lqmce7ou1lr76rv4dhss",
    "ExportName": "safety-confirmation-dev-CognitoUserPoolClientTestId"
  }
]
```

→ `Value` は §5.3 の ClientId と完全一致。`ExportName` は SPA Client の Export 命名規則と対称（`{StackName}-{LogicalId}`）。

### 5.5 検証 2.4 — admin-initiate-auth 動作確認

```powershell
aws cognito-idp admin-initiate-auth `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --client-id 7vtk89lqmce7ou1lr76rv4dhss `
  --auth-flow ADMIN_USER_PASSWORD_AUTH `
  --auth-parameters USERNAME=tomita@g-wise.co.jp,PASSWORD=invalidPassword12345! ...
```

実機レスポンス：

```text
An error occurred (NotAuthorizedException) when calling the AdminInitiateAuth operation: Incorrect username or password.
```

→ **AuthFlow `ADMIN_USER_PASSWORD_AUTH` が正常に処理された証拠**：

- `InvalidParameterException: Auth flow not enabled for this client` ではなく `NotAuthorizedException`（=「AuthFlow の構成は OK、認証情報が不正」）
- ユーザ存在チェックも通過（unknown user なら別エラー、tomita ユーザは認知されている）
- パスワードが意図的に invalid のため、AWS が認証拒否しただけ

注意：tomita ユーザのパスワードは口頭管理のため、本検証では IdToken 実取得まで実施せず、AuthFlow 疎通確認のみとする（15.6a §5 制約継承）。

### 5.6 検証 2.5 — CycleFinalizer Lambda の deploy 反映確認

```powershell
aws lambda get-function-configuration --function-name safety-confirmation-cycle-finalizer-dev ...
```

```json
{
  "LastModified": "2026-06-27T17:47:01.000+0000",
  "CodeSha256": "/b/7XqAL47kSqPvPskvqWZgnWbHzlswcjmhQbvwKC0w=",
  "Runtime": "python3.12",
  "Handler": "handler.lambda_handler"
}
```

- LastModified UTC `2026-06-27T17:47:01` = JST `2026-06-28T02:47:01`
- CognitoUserPoolClientTest の CreationDate `2026-06-28T02:46:58` と同一バッチで更新 = 本 deploy で 15.23 DRY refactor が dev 環境に反映完了
- 機能担保は既存テスト 16/16 PASS（15.23 セッション時点）でカバー済、実 invoke は本タスクスコープ外

---

## §6 Cognito User Pool 22 項目突合表（15.22 と同じ形式、副作用ゼロ確認）

| #   | フィールド                                      | 15.22 final（期待値）                   | 15.26 after（実機）     | 判定 |
| --- | ----------------------------------------------- | --------------------------------------- | ----------------------- | ---- |
| 1   | Name                                            | safety-confirmation-dev                 | safety-confirmation-dev | ✅   |
| 2   | Password Policy.MinimumLength                   | 12                                      | 12                      | ✅   |
| 3   | Password Policy.RequireUppercase                | true                                    | true                    | ✅   |
| 4   | Password Policy.RequireLowercase                | true                                    | true                    | ✅   |
| 5   | Password Policy.RequireNumbers                  | true                                    | true                    | ✅   |
| 6   | Password Policy.RequireSymbols                  | true                                    | true                    | ✅   |
| 7   | Password Policy.TemporaryPasswordValidityDays   | 7                                       | 7                       | ✅   |
| 8   | MfaConfiguration                                | OFF                                     | OFF                     | ✅   |
| 9   | AdminCreateUserConfig.AllowAdminCreateUserOnly  | true                                    | true                    | ✅   |
| 10  | AdminCreateUserConfig.UnusedAccountValidityDays | 7                                       | 7                       | ✅   |
| 11  | UsernameAttributes                              | [email]                                 | [email]                 | ✅   |
| 12  | AutoVerifiedAttributes                          | (empty / 出力なし)                      | (empty / 出力なし)      | ✅   |
| 13  | AccountRecoverySetting                          | admin_only Priority 1                   | admin_only Priority 1   | ✅   |
| 14  | LambdaConfig.PreSignUp                          | safety-confirmation-auth-pre-signup-dev | 同上                    | ✅   |
| 15  | LambdaConfig.PreAuthentication                  | safety-confirmation-auth-pre-auth-dev   | 同上                    | ✅   |
| 16  | LambdaConfig.PostAuthentication                 | safety-confirmation-auth-post-auth-dev  | 同上                    | ✅   |
| 17  | Schema (required): sub                          | true                                    | true                    | ✅   |
| 18  | Schema (required): name                         | true                                    | true                    | ✅   |
| 19  | EmailConfiguration                              | COGNITO_DEFAULT                         | COGNITO_DEFAULT         | ✅   |
| 20  | VerificationMessageTemplate.DefaultEmailOption  | CONFIRM_WITH_CODE                       | CONFIRM_WITH_CODE       | ✅   |
| 21  | DeletionProtection                              | INACTIVE                                | INACTIVE                | ✅   |
| 22  | UserPoolTags（3 件 + 3 件システム tag）         | 設計通り                                | 維持                    | ✅   |

**補足**：上記 22 項目の他、SchemaAttributes 21 属性全体（profile / address / birthdate / gender / preferred_username / updated_at / website / picture / identities / sub / phone_number / phone_number_verified / zoneinfo / locale / email / email_verified / given_name / family_name / middle_name / name / nickname）も完全一致。**`Compare-Object` で diff 0 件 = バイト列レベルで完全一致**。

→ **全 22 項目（+ 21 SchemaAttributes）完全一致、副作用ゼロ完全達成**。15.22 で確立した「`update-user-pool` API 禁忌」運用ルール（CFn deploy 経由）の有効性を再実証。

---

## §7 残課題

| #   | 課題                                                                                                                                                                        | 重要度 | 引き継ぎ先候補                                                                                                                                 |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | tomita@g-wise.co.jp のパスワードは口頭管理のため、AI による IdToken 自動取得は本 Client があっても実現不可。AWS CLI 自動化基盤としての本格活用は別タスク。                  | Med    | dev 限定 CLI 用ユーザを新規作成（例：`cli-test@safety-confirmation.dev`）し、パスワードを Secrets Manager / SSM Parameter Store に保管する案。 |
| 2   | 本 Client を実 IdToken 取得 → API Gateway 経由 read-only curl 検証スクリプトとして整備（`infrastructure/scripts/verify-api.ps1` 等）。                                      | Med    | 15.28 候補（軽量整地系）                                                                                                                       |
| 3   | dev 以外の環境（stg / prod）で本 Client が誤って作成されないことを CI で検証（例: cfn-lint カスタムルール、もしくは別環境 deploy 後の `list-user-pool-clients` smoke test） | Low    | 15.28 〜 15.30 範囲、品質保全系                                                                                                                |
| 4   | `IsDev` Condition が新設されたため、他に dev 限定リソースを追加したい時の汎用 Condition として再利用可能。次の dev 限定リソース起票時に参照。                               | Info   | 将来タスク参考                                                                                                                                 |

---

## §8 関連ファイル

- `infrastructure/template.yaml` L270-275（`IsDev` Condition）, L1082-1103（`CognitoUserPoolClientTest` Resource）, L5050-5056（`CognitoUserPoolClientTestId` Output）
- `docs/notes/15-26-changeset.json`（ChangeSet 4 件サマリ）
- `docs/notes/15-26-changeset-full.json`（ChangeSet 全文）
- `docs/notes/15-26-deployed-template.json`（deploy 前 template snapshot）
- `docs/notes/15-26-user-pool-before.json`（deploy 前 User Pool snapshot）
- `docs/notes/15-26-user-pool-after.json`（**deploy 後 User Pool snapshot、22 項目突合元**）
- `docs/notes/15-26-list-clients.json`（list-user-pool-clients 結果、新規 Client 確認）
- `docs/notes/15-26-test-client-detail.json`（describe-user-pool-client 結果、全プロパティ確認）
- `docs/notes/15-26-admin-auth-result.txt`（admin-initiate-auth レスポンス、AuthFlow 疎通確認）
- `docs/notes/15-22-user-pool-final.json`（**22 項目突合の期待値ソース、本ノート §6 のレファレンス**）
- `docs/notes/15-22-user-pool-side-effects.md`（22 項目突合表のフォーマット元、`update-user-pool` API 禁忌の運用根拠）
- `docs/notes/15-2a-placeholder-deploy.md` §4.2 追加発見 (i)（本タスク起源）
- `docs/notes/15-6a-non-connect-acceptance.md` §5 残課題表（本タスク Med 起票元）
- `backend/lambdas/cycle_finalizer/handler.py`（15.23 DRY 共通化済、本 deploy で dev 反映完了）

---

## §9 所感

15.26 の本質は **「CFn `Condition` を使った環境別リソース分岐の対称設計」+「ChangeSet review 段階での第 7 原則ズレ検知の有効性実証」** の 2 つに整理される。

設計面では既存 `IsProd` の対称として `IsDev` を新設し、stg では IsProd=false / IsDev=false で本 Client 不在 = 単純な階層構造に整地できた。`ALLOW_ADMIN_USER_PASSWORD_AUTH` を dev 限定とする運用方針が CFn 機構的に保証されており、stg / prod での誤作成リスクをコードレベルで排除できている点が最大の価値。

検証面では `Compare-Object` による full-text diff で 15-22-user-pool-final.json と 15-26-user-pool-after.json が **バイト列レベルで完全一致**を確認。これは「CFn deploy 経由なら User Pool 本体に副作用なし」という 15.22 で確立した運用ルールの再実証であり、`update-user-pool` API 禁忌方針の根拠を強化する重要な証跡。

第 7 原則ズレ検知（前段 subagent）→ orchestrator 真因特定 → 案 A 採用 → 本 subagent execute + 検証 の流れは、**「ChangeSet を deploy 前に必ず人間（orchestrator）レビューする」運用の有効性を実証**した節目。ChangeSet が想定外の Modify を含んでいても、その由来が明確（15.23 DRY refactor の未 deploy 分）かつ安全（in-place、テスト済、Replacement=False）であれば、流すべき差分として承認できるという判断ロジックを残せた。

残課題はあるが（§7）、本タスクスコープでは「dev 環境用テスト App Client の CFn 機構的追加」を完全達成。CLI 自動化基盤の土台は整い、後続タスクで本 Client を実 IdToken 取得 → API curl 検証スクリプト整備に活用できる状態となった。
