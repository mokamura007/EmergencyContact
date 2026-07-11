# 管理者ユーザー運用手順

本ドキュメントは、安否確認システムの **管理者ユーザー登録・削除の日常運用**
手順を集約する。退職時 Cognito 削除の詳細は [`privacy.md`](./privacy.md)
§6.1 に、初回セットアップ（管理者ゼロ状態からのブートストラップ）の詳細は
[`deploy.md`](./deploy.md) Step 9 および
[`mock-integration-test.md`](./mock-integration-test.md) §2 に譲る。

## 1. 経路の整理

管理者ユーザー登録には 2 経路が存在する。日常運用では **(B) SPA 経路** を
使い、CLI 経路は初回ブートストラップに限る。

| 経路                                               | 用途                                            | ユーザー体験                                                  | 前提                                                                             |
| -------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| (A) AWS CLI（`aws cognito-idp admin-create-user`） | 初回ブートストラップ（管理者ゼロ状態）のみ      | 一時パスワードは CLI 実行者が手動で受渡                       | AWS 認証情報、`--message-action SUPPRESS` で誤送信抑止可                         |
| (B) SPA「新規社員追加」画面                        | 日常運用（管理者 1 名以上がログイン可能な状態） | 日本語の招待メールが Cognito から自動送信、一時パスワード付き | 既存管理者の SPA ログイン、`POST /employees` が Administrator group で認可される |

初回セットアップ手順（(A) 経路）：[`deploy.md`](./deploy.md) Step 9 の
「管理者ユーザー作成（Cognito）」および
[`mock-integration-test.md`](./mock-integration-test.md) §2「管理者ユーザー
登録（新規メールアドレス）」を参照。以降は本ドキュメントの (B) 経路で完結
する。

## 2. SPA からの新規管理者登録手順（日常運用）

### 2.1 前提

- 既存管理者が Cognito Administrator group に少なくとも 1 名存在し、SPA
  にログインできる状態であること。
- 新規管理者の氏名・電話番号（E.164 形式）・email（Cognito ログイン ID）が
  確定していること。email は AWS Cognito にすでに存在してはならない（同名は
  409 で拒否される）。

### 2.2 手順

1. SPA にログインし、左メニュー「社員マスタ管理」→「新規社員追加」を開く。
2. フォームに以下を入力する：
   - 氏名（1〜128 文字）
   - 電話番号（E.164、例：`+819012345678`）
   - 「管理者権限を付与する」チェックボックスを **ON** にする
   - 展開された「管理者 email」欄にログイン用アドレス（例：`user@example.com`）を入力
3. 「追加する」ボタンを押下すると、以下が同時に走る：
   - Cognito `admin_create_user` が呼ばれ、Administrator group に追加される
   - DynamoDB `Employee-<env>` に `role=admin` + `cognitoSub` 付きで書き込まれる
   - 監査ログに `EMPLOYEE_ADD` と `COGNITO_USER_CREATE` の 2 レコードが記録
     される（`target=employeeId`、`COGNITO_USER_CREATE.extra.cognitoSub` に
     採番された sub を保持）
   - Cognito から日本語の招待メール（件名「【安否確認システム】管理者アカ
     ウントを発行しました」）が入力 email に送信される
4. 新規管理者本人が招待メールを受領し、SPA にアクセスして初回ログインする。
   一時パスワード変更を求められるので、12 文字以上・大文字/小文字/数字/記号
   混在の永続パスワードに変更する。

### 2.3 招待メール仕様

CloudFormation Parameter `AdminCreateUserConfig.InviteMessageTemplate`
（`infrastructure/template.yaml` の `CognitoUserPool` リソース）で定義。

| 項目           | 内容                                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------------------------ |
| 件名           | `【安否確認システム】管理者アカウントを発行しました`                                                               |
| 本文           | 日本語 HTML、ログイン URL / ユーザー名 / 一時パスワード / パスワードポリシー要件 / 誤送信案内 / 自動送信の断り書き |
| プレースホルダ | `{username}`（Cognito username = email）、`{####}`（一時パスワード）が Cognito 側で置換される                      |
| ログイン URL   | 環境別に注入：カスタムドメイン設定時は `https://<DomainName>/`、未設定時は `https://<SpaDistribution.DomainName>/` |
| 送信媒体       | `DesiredDeliveryMediums=["EMAIL"]`（SMS 送信なし）                                                                 |
| 送信元         | Cognito デフォルト（COGNITO_DEFAULT、AWS 提供 from、日次送信上限あり。ボリューム増時は SES 連携を検討）            |

### 2.4 エラー時の挙動

| エラー                                                         | HTTP | 原因                                       | 対処                                                                |
| -------------------------------------------------------------- | ---- | ------------------------------------------ | ------------------------------------------------------------------- |
| `Phone number already registered`                              | 409  | 電話番号重複（論理削除済含む）             | 別の電話番号を使う、または論理削除済の同番号を anonymize して再利用 |
| `Admin email already registered in Cognito`                    | 409  | Cognito に同 email の User が既存          | AWS Console / CLI で User 状態確認、必要なら手動削除後に再試行      |
| `adminEmail is required and must be a valid email address ...` | 400  | email 形式不正（RFC 5322 simplified 違反） | 形式を修正して再試行。SPA 側でも同等の事前検査が走る                |

## 3. 退職時 Cognito 削除の運用

日常運用の対（対称性）。SPA「社員マスタ管理」画面の「論理削除済社員も表示」
トグルを ON にし、対象退職者行の「Cognito 削除」ボタンを押下する。詳細は
[`privacy.md`](./privacy.md) §6.1 を参照。

## 4. CFn deploy による副作用防止（重要）

`docs/notes/15-22-user-pool-side-effects.md` で実証された通り、Cognito
UserPool の `update-user-pool` API は明示指定しなかった一部フィールドを
AWS デフォルト値にリセットする挙動がある。CloudFormation drift detection
は `AdminCreateUserConfig` を比較対象にしないため、drift 検出だけでは
副作用を発見できない。

`InviteMessageTemplate` を含む CFn 変更を deploy する場合は、以下の順序で
副作用検証を必ず行う。

### 4.1 deploy 前 snapshot 取得

```powershell
pwsh -File infrastructure/scripts/verify-cognito.ps1 `
    -EnvironmentName dev `
    -OutputPath cognito-before.json
```

- Stack Output `CognitoUserPoolId` を自動解決
- `aws cognito-idp describe-user-pool` を実行し JSON を保存
- 重要フィールドの現在値をコンソール表示
- **Phase 15.22 由来の regression fields**（`AllowAdminCreateUserOnly` /
  `LambdaConfig` 3 Trigger / `InviteMessageTemplate.Email*`）が既に空 /
  false 化していないかを assert（もし fatal ならその時点で exit 4）

### 4.2 deploy 実行

```powershell
pwsh -File infrastructure/scripts/deploy.ps1 -EnvironmentName dev
```

### 4.3 deploy 後 snapshot 取得 + 突合

```powershell
pwsh -File infrastructure/scripts/verify-cognito.ps1 `
    -EnvironmentName dev `
    -BaselinePath cognito-before.json `
    -OutputPath cognito-after.json
```

- 事前 snapshot と事後 snapshot を byte / semantic 両レベルで比較
- 差分ゼロ → exit 0（成功）
- 差分あり → exit 1、`Compare-Object` の diff を表示

### 4.4 期待される差分（今回の InviteMessageTemplate 追加時）

初回導入時は当然 diff が発生する（`InviteMessageTemplate` が None から
`{ EmailSubject, EmailMessage }` に変わる）。以下だけが差分になれば OK：

- `AdminCreateUserConfig.InviteMessageTemplate.EmailSubject`（None → `【安否確認システム】…`）
- `AdminCreateUserConfig.InviteMessageTemplate.EmailMessage`（None → 日本語 HTML 本文）
- `LastModifiedDate`（更新時刻の差）

これ以外のフィールドに差分が出た場合は Phase 15.22 由来の副作用リセットの
可能性がある。`docs/notes/15-22-user-pool-side-effects.md` §5 の「一括指定
で完全復元」パターンで修復すること。

## 5. トラブルシュート

### 5.1 招待メールが届かない

- Cognito メール送信上限（COGNITO_DEFAULT では日次 50 通、`aws cognito-idp
get-user-pool-mfa-config` 等ではなく AWS Cognito コンソール「メッセージ」
  タブから確認可能）に達している場合、送信されない。SES 連携（
  `EmailConfiguration.EmailSendingAccount=DEVELOPER`）を検討する。
- placeholder（`@example.com`）宛は当然届かない（RFC 2606 予約ドメイン）。
- 迷惑メール振り分けの可能性。宛先ユーザーに迷惑メールフォルダ確認を依頼。

### 5.2 「Admin email already registered in Cognito」エラー

- 過去に同 email で作成し、後に手動削除済でも「削除中」状態が数分残る場合が
  ある。10 分待ってから再試行するか、AWS Console で User 一覧を直接確認。

### 5.3 CFn deploy 後、既存の SPA ログインが動作しなくなった

- Phase 15.22 副作用の可能性。`verify-cognito.ps1 -Mode Compare` で差分を
  確認する。特に `LambdaConfig` の 3 Trigger（`PreAuthentication` /
  `PostAuthentication` / `PreSignUp`）と `AdminCreateUserConfig
.AllowAdminCreateUserOnly` の状態を確認。
- 副作用による欠落が判明した場合は、`docs/notes/15-22-user-pool-side-effects
.md` §5 の一括指定復元手順を実行。
