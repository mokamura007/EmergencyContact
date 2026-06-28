# Task 15.22 — update-user-pool API 副作用検証（15.20 副次効果の整合性確認）

- 完了日: 2026-06-27（セッション 22、tasks.md 15.22）
- 対応者: kiro（orchestrator 直接実行、AWS CLI 経由 + ユーザー指示の y/n × 2 回承認）
- スコープ: dev 環境（Stack `safety-confirmation-dev`、Account 214046906694、ap-northeast-1）
- Connect 依存: なし

---

## 1. タスク背景

15.20 で `aws cognito-idp update-user-pool --lambda-config "..."` を実行。LambdaConfig は意図通り再アタッチされたが、AWS Cognito の update-user-pool API が指定外フィールドをリセットしていないかを客観確認する。

---

## 2. 検証フロー（3 ステップ）

| Step | 内容                                                                                   | 結果                                                                                                                                |
| ---- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `describe-user-pool` 取得 + template.yaml 12 フィールド突合                            | **1 件 mismatch 発見**：`AdminCreateUserConfig.AllowAdminCreateUserOnly = false`（template.yaml 期待 = true、Requirement 1.9 違反） |
| 2    | `update-user-pool --admin-create-user-config "AllowAdminCreateUserOnly=true"` 単独実行 | ExitCode=0、AdminCreateUserConfig 修正成功、**LambdaConfig が空 {} に副作用リセット**（致命的）                                     |
| 3    | `update-user-pool --cli-input-json` 一括指定で 9 フィールド明示復元                    | ExitCode=0、**全フィールド完全一致達成、副作用なし**                                                                                |

---

## 3. 重要な発見：CFn drift detection が AdminCreateUserConfig を検出できない

15.21 の `detect-stack-drift` 結果では「CognitoUserPool が IN_SYNC（drift なし）」と判定されたが、本タスクの直接 API 突合で `AdminCreateUserConfig.AllowAdminCreateUserOnly` の重大 mismatch が発覚。

→ **AWS CloudFormation drift detection は AWS::Cognito::UserPool の AdminCreateUserConfig を比較対象にしていない**（既知の AWS 制約）。15.22 のような直接 API 突合でしか発見できない。

15.21 と 15.22 は補完関係：

- 15.21 = CFn 視点での drift（CFn が管理するプロパティ範囲）
- 15.22 = Cognito API 視点での実機 vs template.yaml 突合（CFn 管理外プロパティ含む）

---

## 4. 確定された AWS API バグ的挙動

第 17 原則対称性推論による検証：

| 操作         | 指定したフィールド           | リセットされた他フィールド                                         |
| ------------ | ---------------------------- | ------------------------------------------------------------------ |
| 15.20        | `--lambda-config`            | AdminCreateUserConfig.AllowAdminCreateUserOnly が false 化（推定） |
| 15.22 Step 2 | `--admin-create-user-config` | **LambdaConfig が空 {} 化（実証済）**                              |

→ **`update-user-pool` API は、明示指定しなかった一部フィールドを AWS デフォルト値にリセットする**（公式ドキュメント上は曖昧、Forums で複数報告あり）。

### 4.1 影響範囲（修復前の dev 環境）

- ✅ Cognito 認証フロー自体は機能（Lambda Trigger なしでも login 可能）
- ❌ AUTH_SUCCESS 監査ログ書込（PostAuth Lambda）機能停止
- ❌ ロックアウト判定（PreAuth Lambda）機能停止
- ❌ 管理者作成ゲート（PreSignUp Lambda）機能停止
- ❌ Requirement 1.9 違反（自己サインアップ有効状態）

### 4.2 修復ステップ 3 で採用した戦略

`--cli-input-json` 一括指定で**9 フィールドを明示記述**して他フィールド副作用を最小化：

- Policies（PasswordPolicy 6 項目）
- DeletionProtection
- LambdaConfig（3 Trigger）
- AutoVerifiedAttributes（empty）
- VerificationMessageTemplate
- EmailConfiguration
- AdminCreateUserConfig.AllowAdminCreateUserOnly
- AccountRecoverySetting
- UserAttributeUpdateSettings

UserPoolTags は CFn 管理 system tag（`aws:cloudformation:*`）を含むため意図的に **指定しない**（AWS が現状維持してくれる、結果として副作用なし確認済）。

---

## 5. 修復後の全フィールド突合表

| フィールド                                         | template.yaml 期待                      | 修復後実機              | 判定            |
| -------------------------------------------------- | --------------------------------------- | ----------------------- | --------------- |
| Name                                               | safety-confirmation-${env}              | safety-confirmation-dev | ✅              |
| Password Policy.MinimumLength                      | 12                                      | 12                      | ✅              |
| Password Policy.RequireUppercase                   | true                                    | true                    | ✅              |
| Password Policy.RequireLowercase                   | true                                    | true                    | ✅              |
| Password Policy.RequireNumbers                     | true                                    | true                    | ✅              |
| Password Policy.RequireSymbols                     | true                                    | true                    | ✅              |
| Password Policy.TemporaryPasswordValidityDays      | 7                                       | 7                       | ✅              |
| MfaConfiguration                                   | OFF                                     | OFF                     | ✅              |
| **AdminCreateUserConfig.AllowAdminCreateUserOnly** | **true**                                | **true**                | ✅ **修正成功** |
| AdminCreateUserConfig.UnusedAccountValidityDays    | 7                                       | 7                       | ✅              |
| UsernameAttributes                                 | [email]                                 | [email]                 | ✅              |
| AutoVerifiedAttributes                             | (empty)                                 | (empty)                 | ✅              |
| AccountRecoverySetting                             | admin_only Priority 1                   | admin_only Priority 1   | ✅              |
| **LambdaConfig.PreSignUp**                         | safety-confirmation-auth-pre-signup-dev | **同上**                | ✅ **復元**     |
| **LambdaConfig.PreAuthentication**                 | safety-confirmation-auth-pre-auth-dev   | **同上**                | ✅ **復元**     |
| **LambdaConfig.PostAuthentication**                | safety-confirmation-auth-post-auth-dev  | **同上**                | ✅ **復元**     |
| Schema (required): sub                             | true                                    | true                    | ✅              |
| Schema (required): name                            | true                                    | true                    | ✅              |
| EmailConfiguration                                 | COGNITO_DEFAULT                         | COGNITO_DEFAULT         | ✅              |
| VerificationMessageTemplate.DefaultEmailOption     | CONFIRM_WITH_CODE                       | CONFIRM_WITH_CODE       | ✅              |
| DeletionProtection                                 | INACTIVE                                | INACTIVE                | ✅              |
| UserPoolTags（3 件 + 3 件システム tag）            | 設計通り                                | 維持                    | ✅              |

→ **全 22 項目完全一致、修復完全成功**。

---

## 6. 教訓と運用ルール

### 6.1 update-user-pool API の禁忌

**今後 `aws cognito-idp update-user-pool` API は使わない運用ルールを確立する**。代替手段：

| 推奨度 | 方法                                                                            | 副作用リスク                                      |
| ------ | ------------------------------------------------------------------------------- | ------------------------------------------------- |
| ★★★    | **CFn template.yaml 修正 + `aws cloudformation deploy` 経由**                   | ほぼなし（CFn が全フィールド管理）                |
| ★★     | **AWS Console UI からの手動修正**                                               | UI が必要なフィールドのみ更新する仕様、副作用なし |
| ★      | `aws cognito-idp update-user-pool --cli-input-json` 一括指定で全フィールド明示  | 中（指定漏れ即副作用）                            |
| ❌     | `aws cognito-idp update-user-pool --lambda-config "..."` 等の単独フィールド指定 | **絶対回避**（他フィールド副作用既知）            |

### 6.2 検証ルール（CognitoUserPool 系の運用）

- CFn drift detection は **AdminCreateUserConfig を比較しない**ため、CognitoUserPool 系は **定期的な describe-user-pool 直接突合**で監視
- 突合スクリプトは `infrastructure/scripts/verify-cognito.ps1`（or .sh）として将来整備候補（15.28 候補、軽量整地）

---

## 7. 関連ファイル

- `docs/notes/15-22-user-pool-raw.json`（修正前 before snapshot、AllowAdminCreateUserOnly=false、LambdaConfig 3 件保持）
- `docs/notes/15-22-user-pool-after.json`（Step 2 直後 snapshot、AllowAdminCreateUserOnly=true、LambdaConfig {} 空 = 致命的副作用状態）
- `docs/notes/15-22-user-pool-final.json`（Step 3 完了 snapshot、全フィールド完全一致）
- `docs/notes/15-22-update-payload.json`（Step 3 で使用した一括指定 JSON）
- `docs/notes/15-22-all-drift.json`（15.21 と共用、CognitoUserPool が drift detection 対象外であることの根拠）
- `docs/notes/15-20-postauth-import-fix.md`（15.20 LambdaConfig 再アタッチ記録、本タスクの起源）
- `infrastructure/template.yaml` L996-1075（CognitoUserPool 定義、突合元）

---

## 8. Done When 充足

| 条件                               | 状態                            |
| ---------------------------------- | ------------------------------- |
| describe-user-pool 取得            | ✅                              |
| template.yaml 定義との比較表作成   | ✅（§5）                        |
| 副作用なし or 差異の特定と対応完了 | ✅ **差異 1 件発見 + 対応完了** |

---

## 9. 第 7 原則ズレ検知 3 件

| #   | 内容                                                                                         | 対応                                                             |
| --- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| 1   | `AdminCreateUserConfig.AllowAdminCreateUserOnly = false`（実機）vs `true`（template.yaml）   | 停止 → user_input で修正方針 y/n → (A) 採用 → 単独指定で実行     |
| 2   | Step 2 で update-user-pool 副作用により LambdaConfig が空化 = 認証経路破綻                   | 停止 → user_input で修復方針 y/n → (α) 採用 → 一括指定で完全復元 |
| 3   | CFn drift detection が CognitoUserPool の AdminCreateUserConfig を検出しない（15.21 と矛盾） | 本タスク §3 で明文化、15.21 と 15.22 の補完関係を確立            |

---

## 10. 所感

15.22 の本質的価値は「**CFn drift detection の限界補完**」と「**update-user-pool API のリセット副作用の実証**」の 2 つに整理された。15.21 の drift detection 単独では「IN_SYNC = OK」と誤判定される領域に、Cognito API 直接突合で発見できる重大 mismatch が存在することを実証した節目。

特に副作用検出の手順自体が **第 7 原則ズレ検知 → 停止 → ユーザー y/n → 修復 → 検証 → 副作用再検出 → ループ** で 2 回発動し、第 17 原則対称性推論「`--lambda-config` 指定 → AdminCreateUserConfig リセット」「`--admin-create-user-config` 指定 → LambdaConfig リセット」の双方向検証で AWS API バグ的挙動を確定。今後 `update-user-pool` API を本プロジェクトで使わない運用ルールの根拠となる。

実機修復は完全成功（22 項目完全一致）、Requirement 1.9 違反状態は解消、認証経路（3 Lambda Trigger）も完全復元。dev-login-followup §3 残作業 ① + ② で確認した PostAuth + 関連 Trigger の整備が再度危機に陥ったが本セッション内で完全復旧。

副次タスク候補（15.28）：定期的な Cognito User Pool 構成突合スクリプト（`infrastructure/scripts/verify-cognito.ps1`）整備、CI ステップ組込み。
