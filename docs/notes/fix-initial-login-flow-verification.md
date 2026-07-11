# issue #3 初回ログインフロー修正 実機検証エビデンス

- Spec: `.kiro/specs/fix-initial-login-flow/`（requirements.md / design.md / tasks.md）
- Issue: [mokamura007/EmergencyContact#3](https://github.com/mokamura007/EmergencyContact/issues/3)
- 対象環境: dev（Stack `safety-confirmation-dev`、Account `214046906694`、ap-northeast-1）
- 記録開始: 2026-07-10（Task 6, 7 CLI 側作業を Kiro が自走実施）

## 1. CLI 側作業の結果（自動化済み）

### 1.1 SPA ビルド + デプロイ（Task 6）

- ビルド：`npm run build` 成功、`frontend/dist/` 生成
  - 生成物：
    - `index.html`（0.47 kB）
    - `assets/index-YTLpAtau.css`（0.30 kB）
    - `assets/index-DXE-hZnW.js`（335.17 kB）+ `.map`（1424.74 kB）
- S3 sync：`aws s3 sync frontend/dist/ s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/ --delete` 成功
  - 削除：`assets/index-CvpLT3AS.js` + `.map`（旧バンドル）
  - アップロード：新バンドル 4 ファイル
- CloudFront invalidation：
  - Distribution: `EAXOBS3AIJQHH`
  - Invalidation ID: `IE1M5D4HKJ32D28QGLKFR5QUNJ`
  - Status: `Completed`（Kiro セッション内で確認済）

### 1.2 検証用ユーザー作成（Task 7 前半）

- Username: `issue3-verify@example.com`
- Sub: `f7041a48-b041-703c-75de-aa03e1681bc8`
- UserStatus: `FORCE_CHANGE_PASSWORD`
- Enabled: `true`
- Groups: `[Administrator]`
- 一時パスワード: `TempPass!2026`（本ファイルには記載しないのが望ましいが、docs/notes/dev-login-followup.md と同慣行）
- `email_verified`: `true`（`admin-create-user` 時に付与）

## 2. 実機ブラウザ検証（Task 7 後半・Task 8、ユーザー記入枠）

以下は Kiro からは自動化できないため、ユーザーがブラウザでハードリロード（Ctrl+Shift+R）を実施した上で確認し、下表を埋める。

### 2.1 `FORCE_CHANGE_PASSWORD` フロー（Requirement 1、Task 7）

| 項目                                                            | 期待値                                                     | 実測       | エビデンス               |
| --------------------------------------------------------------- | ---------------------------------------------------------- | ---------- | ------------------------ |
| a. ログイン画面表示                                             | `メールアドレス` / `パスワード` 入力欄と「ログイン」ボタン | （未記入） | スクリーンショット添付枠 |
| b. `issue3-verify@example.com` + `TempPass!2026` 入力後の遷移先 | `/new-password`（初回パスワード変更画面）                  | （未記入） | スクリーンショット添付枠 |
| c. 新パスワード設定成功後の遷移先                               | `/`（管理者ダッシュボード）                                | （未記入） | スクリーンショット添付枠 |
| d. Console ログ                                                 | `Login failed:` は **出ない**（成功系のため）              | （未記入） |                          |
| e. Network タブの `main.[hash].js`                              | `assets/index-DXE-hZnW.js`                                 | （未記入） |                          |

新パスワード：Cognito パスワードポリシー（8 文字以上、大文字・小文字・数字・記号）を満たす任意の値を設定して確認する。

### 2.2 `CONFIRMED` フロー（Requirement 1.1 外、Task 8）

| 項目                                                                | 期待値           | 実測       | エビデンス |
| ------------------------------------------------------------------- | ---------------- | ---------- | ---------- |
| a. `tomita@g-wise.co.jp`（`CONFIRMED`）＋正しいパスワードでログイン | `/` 直接遷移     | （未記入） |            |
| b. Console ログ                                                     | 出ない（成功系） | （未記入） |            |

### 2.3 誤パスワード（Requirement 2.1 / 2.4、Task 8）

| 項目              | 期待値                                                                                          | 実測       | エビデンス   |
| ----------------- | ----------------------------------------------------------------------------------------------- | ---------- | ------------ |
| a. 表示メッセージ | 「メールアドレスまたはパスワードが正しくありません。」                                          | （未記入） |              |
| b. Console ログ   | `Login failed: { errorName: 'AuthenticationFailedError', code: 'NotAuthorizedException', ... }` | （未記入） | Console 抜粋 |

### 2.4 未知エラー系（オプション、Requirement 2.2 の効果確認）

該当ケースが実機で発生した場合、Console に `Login failed:` プレフィックスで code が出るはずである。運用中に issue #3 と類似の症状が出た際は、以下を記録する：

- Console の `Login failed:` の `code` フィールド
- 画面表示メッセージ（`（コード: XXX）` の XXX 部分）

## 3. 再発時の切り分けフロー

1. ブラウザ F12 → Console タブで `Login failed:` を検索
2. `code` の値を確認：
   - `NotAuthorizedException` / `UserNotFoundException` → パスワード / ユーザー名相違
   - `UserLambdaValidationException` → PostAuthentication Lambda 障害。`docs/notes/15-20-postauth-import-fix.md` §3.2 の切り分け手順を参照
   - `NEW_PASSWORD_REQUIRED` 型結果なのに `AuthenticationFailedError` が出る → ε-2 修正が壊れている可能性。`cognitoAuthProvider.ts` L122 前後を精査
   - `MissingAuthConfigError` → `.env.local` の `VITE_COGNITO_*` 未設定
   - 未知 code → 汎用メッセージ末尾に付いた `（コード: XXX）` の XXX で Cognito ドキュメントを検索
3. Cognito 側の状態確認：

   ```
   aws cognito-idp admin-get-user --user-pool-id ap-northeast-1_5uYfaQMLJ --username <email> --profile AWS-security-check --region ap-northeast-1
   ```

   - `UserStatus`：`FORCE_CHANGE_PASSWORD` / `CONFIRMED` / `RESET_REQUIRED` 等
   - `Enabled`：`false` なら管理者側で有効化が必要

4. PostAuthentication Trigger が User Pool にアタッチされているか：
   ```
   aws cognito-idp describe-user-pool --user-pool-id ap-northeast-1_5uYfaQMLJ --profile AWS-security-check --region ap-northeast-1 --query "UserPool.LambdaConfig"
   ```

## 4. 変更ファイル一覧（issue #3 対応）

- 新規：`frontend/src/routing/loginErrors.ts`
- 新規：`frontend/src/routing/loginErrors.test.ts`（17 件、all green）
- 改修：`frontend/src/routing/LoginPage.tsx`（`translateLoginError` 導入 + `console.error` 追加）
- 改修：`frontend/src/routing/LoginPage.test.tsx`（3 テスト追加、既存 1 テストのアサーション更新）
- 副次修正：`frontend/src/employees/EmployeeFormPage.test.tsx` L162（tsc 型推論回避、build 通過のため）
- 新規：`.kiro/specs/fix-initial-login-flow/requirements.md`
- 新規：`.kiro/specs/fix-initial-login-flow/design.md`
- 新規：`.kiro/specs/fix-initial-login-flow/tasks.md`
- 新規：`docs/notes/fix-initial-login-flow-verification.md`（本ファイル）

## 5. テスト結果（Kiro セッション内で実施）

- `frontend/` `npm run test` → 31 files / 338 tests all pass
- `frontend/` `npm run typecheck` → エラー 0（副次修正後）
- `frontend/` `npm run lint` → 既存 8 errors 存置（本 spec スコープ外の既存問題）
- `frontend/` `npm run build` → 成功、`dist/` 生成

## 6. 残作業（ユーザー実施）

- [ ] 2.1 表を実機ブラウザで再現して埋める（スクリーンショット取得）
- [ ] 2.2 表を実機ブラウザで再現して埋める
- [ ] 2.3 表を実機ブラウザで再現して埋める
- [ ] 完了確認後、GitHub Issue #3 に本ファイルへのリンクを含む完了コメントを投稿し Close
- [ ] （任意）テストユーザー `issue3-verify@example.com` の削除：
  ```
  aws cognito-idp admin-delete-user --user-pool-id ap-northeast-1_5uYfaQMLJ --username issue3-verify@example.com --profile AWS-security-check --region ap-northeast-1
  ```

## 7. 再修正（Kiro セッション 2 回目、2026-07-10 後半）

### 7.1 実機再現結果（1 回目デプロイ後）

- 実施：`issue3-verify@example.com` + `TempPass!2026` でログイン
- 実測：画面に「ログインに失敗しました。時間をおいて再度お試しください。」（**コード併記なし**）
- 分析：初版 `translateLoginError` の未分類経路（`return GENERIC_PREFIX` のみ）に落ちていた。旧バンドルキャッシュか、生 SDK エラーが catch に到達しているかのいずれか。

### 7.2 再修正内容

- `frontend/src/routing/loginErrors.ts` に `extractErrorIdentifier(err)` を追加
- `translateLoginError` の未分類経路も「（コード: XXX）」併記に変更
- `LoginPage.tsx` の `console.error` に生 err オブジェクトを第3引数追加
- テスト 40 件 all green（`loginErrors.test.ts` に 17 件追加、`LoginPage.test.tsx` に 1 件追加）

### 7.3 再デプロイ状態

- 新バンドル：`assets/index-B8hmR_4j.js`（旧 `index-DXE-hZnW.js` は削除）
- CloudFront invalidation：`I6OVJXMQ1QDU9ZTR41UPDYGUEG`
- 実機再確認は 8.1 表に記録

## 8. 再検証結果（ユーザー記入枠、2 回目）

### 8.1 再確認項目

| 項目                            | 期待値                                                                         | 実測       | エビデンス         |
| ------------------------------- | ------------------------------------------------------------------------------ | ---------- | ------------------ |
| a. 画面表示メッセージ           | `/new-password` へ遷移（成功）or 汎用文言 + `（コード: XXX）` の併記（失敗時） | （未記入） | スクリーンショット |
| b. F12 Console `Login failed:`  | 出る（失敗系のみ）、第2引数の object + 第3引数の生 err オブジェクトを展開可能  | （未記入） | Console 抜粋       |
| c. F12 Network `main.[hash].js` | `assets/index-B8hmR_4j.js`（2 回目バンドル）                                   | （未記入） |                    |
| d. XXX（コード）の値            | 期待は `NEW_PASSWORD_REQUIRED` に遷移して該当なし。もし失敗なら記録            | （未記入） |                    |

### 8.2 XXX の値による切り分け（再修正後）

- `NotAuthorizedException` / `UserNotFoundException` → 資格情報の誤り、パスワードを再確認
- `UserLambdaValidationException` → PostAuthentication Lambda 障害。`docs/notes/15-20-postauth-import-fix.md`
- `NewPasswordRequiredError` → 「システム状態が矛盾しています。」表示（ε-2 修正の revert が疑われる）
- `object` / `Error` / SDK 内部型 → 生 SDK エラーが catch に到達。`console.error` 第3引数のオブジェクトで詳細確認
- 何も表示されない（前回同様の症状） → **ブラウザキャッシュに旧バンドルが残っている疑いが濃厚**。DevTools → Network タブで `main.js` のファイル名を確認、`index-B8hmR_4j.js` 以外なら Ctrl+Shift+R でハードリロード + シークレットウィンドウ試行

## 9. 真因判明 → 再々修正（Kiro セッション 3 回目、2026-07-10 終盤）

### 9.1 実機再現結果（2 回目デプロイ後）

- 画面表示：「ログインに失敗しました。時間をおいて再度お試しください。（**コード: DataCloneError**）」
- Network タブ：`cognito-idp.ap-northeast-1.amazonaws.com` へのアクセス 2 回（`InitiateAuth` + `RespondToAuthChallenge`）→ Cognito 認証は成功
- Console `Login failed:` の第3引数：ユーザーは Console の見方が不明で確認できず

### 9.2 真因

`LoginPage.tsx` 初版の `navigate('/new-password', { state: { challenge: result } })` で、React Router が history.state に `structuredClone` する際に `challenge.complete` 関数を clone できず `DataCloneError` を投げていた。ε-2 修正時に見落とされていた Web API 制約。

### 9.3 対応

`frontend/src/auth/authChallengeStore.ts`（モジュールスコープの一時ストア）を新設し、challenge を history.state に載せずに受け渡す。`LoginPage` は `setPendingChallenge(result)` → `navigate('/new-password')`、`NewPasswordPage` は `consumePendingChallenge()` で取り出す。

### 9.4 再々デプロイ状態

- 新バンドル：`index-4yw9v3HS.js`（144 modules）
- CloudFront invalidation：`IEVU5LGW101US6VC8LQWQ7ZAHF`

## 10. Console 出力の見方（ユーザーからの質問「どれを見ればいい」への回答）

F12 で DevTools を開いた後の手順：

1. 上部タブから **Console** を選択
2. 左サイドバーの Level フィルタで **Errors** を有効化（既定で ON）
3. ログイン試行後、次のような 3 行構成のログが出る：

   ```
   Login failed:  {errorName: 'AuthenticationFailedError', code: '...', message: '...'}  Error: ...
   ```

   - 第 1 引数：文字列 `Login failed:`
   - 第 2 引数：構造化 object（`{...}` を左の三角で展開すると `errorName` / `code` / `message` が見える）
   - 第 3 引数：生 err オブジェクト（`Error: ...` の左の三角を展開すると stack trace / name / message / code / cause など SDK 内部プロパティが全て見える）

4. 特に見るべきは **第 2 引数の `code` フィールド**。ここに `NotAuthorizedException` / `DataCloneError` / `UserLambdaValidationException` などが入る。
5. 第 3 引数の生 err は「同じ code は取れたが原因が絞れない」ときに展開して SDK 内部プロパティ（`__type` / `statusCode` / `retryable`）を確認する用途。

もし Console に何も表示されない場合、以下を疑う：

- Level フィルタで Errors が OFF になっている → チェックを付ける
- Console タブではなく Network タブを見ている → タブを切り替え
- ページリロード時に Console が clear されている → 「Preserve log」を ON にしてから操作

## 11. 再検証（ユーザー記入枠、3 回目、`index-4yw9v3HS.js` 版）

| 項目                                             | 期待値                                                                                                         | 実測       | エビデンス         |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- | ---------- | ------------------ |
| a. Network `index-*.js`                          | `index-4yw9v3HS.js`                                                                                            | （未記入） |                    |
| b. ログイン試行後の画面                          | `/new-password` へ遷移して初回パスワード変更フォーム表示                                                       | （未記入） | スクリーンショット |
| c. 新パスワード（英大小数記号 8 文字以上）設定 → | 「初回パスワード変更」画面から `/`（ダッシュボード）へ遷移                                                     | （未記入） | スクリーンショット |
| d. Console `Login failed:`                       | 成功系のため出ない                                                                                             | （未記入） |                    |
| e. cognito-idp アクセス回数                      | 3 回（InitiateAuth / RespondToAuthChallenge PASSWORD_VERIFIER / RespondToAuthChallenge NEW_PASSWORD_REQUIRED） | （未記入） |                    |

もし依然として (b) で `/new-password` に遷移せずエラー表示のままなら、Console の「code: XXX」の XXX を教えてください。DataCloneError 以外の別要因の可能性があります。

## 12. 新パスワード設定時の NotAuthorizedException 対応（Kiro セッション 4 回目、2026-07-10 終盤）

### 12.1 実機報告

- `/new-password` 遷移までは成功（issue #3 の DataCloneError は解消済み）
- 新パスワードに一時パスワードと同じ値（`TempPass!2026`）を入力 → `completeNewPasswordChallenge` が `NotAuthorizedException`
- 画面表示：「セッションが無効になりました。再度ログインしてください。」

### 12.2 真因仮説

Cognito は `RespondToAuthChallenge` の `NEW_PASSWORD_REQUIRED` 応答で「新パスワード = 一時パスワード」を拒否する。この場合 SRP セッションも無効化されるため再ログインが必要になる（`NotAuthorizedException` の副次効果）。

### 12.3 対応

- `NewPasswordPage.tsx` の UI に注意書き追加：「※ 一時パスワードとは異なる値を設定してください」
- `translateCompleteError` の `NotAuthorizedException` メッセージを 3 原因候補（同一パスワード / セッション期限切れ / 試行上限）と復旧手順で具体化
- 未知コードも「（コード: XXX）」併記に統一

### 12.4 デプロイ状態

- 新バンドル：`index-CaFRSxz2.js`
- CloudFront invalidation：`I5PGYMSNZBL1FMENVUEHL3UXRN`

## 13. 再検証（ユーザー記入枠、4 回目、`index-CaFRSxz2.js` 版）

### 13.1 手順

`issue3-verify@example.com` の状態は既に SRP セッションを消費しているため、テストユーザーを削除 → 再作成が最も確実です：

```powershell
# 削除
aws cognito-idp admin-delete-user `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --username issue3-verify@example.com `
  --profile AWS-security-check --region ap-northeast-1

# 再作成（FORCE_CHANGE_PASSWORD、Administrator グループ）
aws cognito-idp admin-create-user `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --username issue3-verify@example.com `
  --user-attributes Name=email,Value=issue3-verify@example.com Name=email_verified,Value=true Name=name,Value=issue3-verify `
  --temporary-password "TempPass!2026" `
  --message-action SUPPRESS `
  --profile AWS-security-check --region ap-northeast-1

aws cognito-idp admin-add-user-to-group `
  --user-pool-id ap-northeast-1_5uYfaQMLJ `
  --username issue3-verify@example.com `
  --group-name Administrator `
  --profile AWS-security-check --region ap-northeast-1
```

### 13.2 テストパスワード（一時とは異なる値）

- 一時：`TempPass!2026`
- **新（別値）**：例 `NewPass2026!` / `Kiro-Verify1!` / `Issue3Done@2026` 等（英大小 + 数字 + 記号、8 文字以上、`TempPass!2026` と異なる値）

### 13.3 再確認項目

| 項目                                      | 期待値                                                                                                      | 実測       | エビデンス         |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------- | ------------------ |
| a. Network `index-*.js`                   | `index-CaFRSxz2.js`                                                                                         | （未記入） |                    |
| b. `/new-password` UI 上部の注意書き      | 「一時パスワードとは異なる値を設定してください」表示                                                        | （未記入） | スクリーンショット |
| c. 新パスワード（別値）入力 → 設定        | ダッシュボード `/` へ遷移                                                                                   | （未記入） |                    |
| d. 万が一同じ値で送信した場合のメッセージ | 「(1) 新パスワードが一時パスワードと同じ、(2) SRP 認証セッションの期限切れ、(3) 試行上限到達」の 3 候補開示 | （未記入） |                    |

## 14. SRP challenge Session 1 回制限（Kiro セッション 5 回目、真因訂正）

### 14.1 実機報告

- 一時と異なる値 `TempPass!202607` を入力 → 同じエラー「認証セッションが無効化されました」（前回強化した NotAuthorizedException メッセージが表示）
- 前回の私の仮説「新パスワード = 一時パスワード拒否」は反証された

### 14.2 訂正真因

Cognito の SRP challenge Session は 1 回限り。前回の `TempPass!2026` 送信で SDK 内部の Session 値が消費され、以降その challenge オブジェクトの `complete()` は必ず `NotAuthorizedException`。同一パスワードかどうかは無関係。

### 14.3 対応

`NewPasswordPage.tsx` に：

- `sessionInvalid: boolean` state 追加。`NotAuthorizedException` 検知時 true
- 入力欄・送信ボタンを disable
- 「ログイン画面に戻る」ボタンを表示
- エラーメッセージを「認証セッションが無効化されました。同じセッションでは再試行できない...」に更新
- UI 注意書きに「認証セッションは 1 回のみ有効」を追加
- `NewPasswordPage.test.tsx` に 2 件追加（合計 12 件 all green）

### 14.4 デプロイ状態

- 新バンドル：`index-B5ShzSMP.js`
- CloudFront invalidation：`I5IP0MVUSU57BM8SQF1EYBWZZ3`

### 14.5 テストユーザー状態確認

`aws cognito-idp admin-get-user` で確認：

- `UserStatus: FORCE_CHANGE_PASSWORD`（維持）
- `Enabled: true`

つまり **ユーザー再作成は不要**、単純にログイン画面から再ログインすれば新しい SRP セッションが発行され、再チャレンジ可能。

## 15. 再検証（ユーザー記入枠、5 回目、`index-B5ShzSMP.js` 版）

### 15.1 手順（テストユーザー再作成 **不要**）

1. Ctrl+Shift+R でハードリロード
2. Network タブで `index-B5ShzSMP.js` を確認
3. `issue3-verify@example.com` + `TempPass!2026` でログイン
4. `/new-password` 遷移後、UI に「認証セッションは 1 回のみ有効」の注意書きが追加表示されることを確認
5. **一度で** `TempPass!2026` と異なる新パスワード（例：`NewPass2026!`）を入力し「パスワードを設定」
6. ダッシュボード `/` へ遷移すれば issue #3 完全解消

### 15.2 万が一またエラーが出た場合

- UI に「ログイン画面に戻る」ボタンが出現するはず（再送信は disable）
- そのボタンを押して、ログイン画面から再度ログインし、直後に別の新しいパスワードで一度で入力
- Console に別の `code: XXX` が出れば、その値を教えてください

## 16. 真因確定（Kiro セッション 7 回目、CLI 直接検証）

### 16.1 ユーザーからの指示「別視点で見直せ」

これまで 3 回連続で真因推定を外していたため、SPA / SDK レイヤーを離れて AWS CLI で Cognito を直接検証する方針に切替。

### 16.2 検証手順

`safety-confirmation-cli-test-dev` App Client（`ADMIN_USER_PASSWORD_AUTH` 許可）を活用：

- **検証 1（6 巡目バンドル相当、`attrs = {}`）**：`admin-respond-to-auth-challenge` に `USERNAME` + `NEW_PASSWORD` のみを渡す
- **検証 2（5 巡目バンドル相当、`attrs = { email, name }`）**：`userAttributes.email` + `userAttributes.name` も渡す

### 16.3 結果

- **検証 1**：`AuthenticationResult` が返り認証成功。`UserStatus: CONFIRMED` に更新
- **検証 2**：Cognito が明示的エラーを返した：
  ```
  An error occurred (NotAuthorizedException) when calling the AdminRespondToAuthChallenge operation:
  Cannot modify an already provided email
  ```

### 16.4 確定した真因

Cognito は `completeNewPasswordChallenge` 時に **既に提供済みの `email` 属性を含めて送信すると変更試行として拒否** する。Amplify のドキュメントで広く紹介されている「`newPasswordRequired` コールバックの `userAttributes` から `email_verified` を delete して残りを送る」慣例は Cognito のこの仕様と実は競合。ε-2 修正で採用した SDK 慣例が失敗の根本原因だった。

### 16.5 現在の状態

- 6 巡目バンドル `index-_1ocmxGG.js`（`completeNewPasswordChallenge(newPassword, {}, callbacks)`）が CloudFront で配信中。CLI 検証で正常動作を確認済み。
- 過去の実機失敗（4 巡目 / 5 巡目バンドルでの `NotAuthorizedException`）は全て `email` 再送信に起因。

### 16.6 テストユーザー状態

- `issue3-verify@example.com`：CLI 検証 1 で `CONFIRMED` になり、パスワード `NewPass2026!`
- `issue3-attr-test@example.com`：CLI 検証 2 でセッション消費（`FORCE_CHANGE_PASSWORD` のまま）
- 実機再検証にはさらに新規テストユーザーの作成が必要
