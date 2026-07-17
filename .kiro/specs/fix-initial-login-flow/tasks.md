# Implementation Plan

本タスクリストは `.kiro/specs/fix-initial-login-flow/requirements.md` の Requirement 1〜3 を満たすための実装手順である。既存 `.kiro/specs/safety-confirmation-system/` に対する追加作業として扱い、コード修正は `frontend/` 配下に閉じる。

- [x] 1. `translateLoginError` 純粋関数を新規作成
  - `frontend/src/routing/loginErrors.ts` を新規作成
  - `AuthenticationFailedError` / `MissingAuthConfigError` / `NewPasswordRequiredError` / 未知値それぞれに対する日本語メッセージ変換を実装
  - 既知 6 code（`NotAuthorizedException`, `UserNotFoundException`, `PasswordResetRequiredException`, `UserLambdaValidationException`, `TooManyRequestsException`, `LimitExceededException`, `UserNotConfirmedException`）に個別分岐、未知コードは汎用メッセージ + `（コード: XXX）` 併記
  - _Requirements: 2.2, 2.3, 2.4, 2.5_
  - _Design: エラー分岐の拡張設計 / 疑似コード / モジュール配置_
  - _Done When: `frontend/src/routing/loginErrors.ts` が存在し、TypeScript コンパイル成功、Lint 0 エラー_

- [x] 2. `translateLoginError` の単体テストを追加
  - `frontend/src/routing/loginErrors.test.ts` を新規作成
  - エラー分類表の全ケース（既知 8 種 + 未知コード + `MissingAuthConfigError` + `NewPasswordRequiredError` fallback + 非 Error 値）を網羅
  - 各分岐について、returned string が Requirement 2.4 の日本語メッセージと完全一致することを検証
  - _Requirements: 2.4, 2.5_
  - _Design: 単体テスト（純粋関数）_
  - _Done When: `vitest --run src/routing/loginErrors.test.ts` が全件 green、分岐カバレッジ 100%_

- [x] 3. `LoginPage.tsx` の `catch` 分岐を `translateLoginError` 経由に置換
  - `frontend/src/routing/LoginPage.tsx` を改修
  - 既存の catch 内 if/else 分岐を `setErrorMessage(translateLoginError(err))` に集約
  - catch の先頭で `console.error('Login failed:', { errorName, code, message })` を出力（入力値は含めない）
  - _Requirements: 2.1, 2.3, 2.4_
  - _Design: エラー分岐の拡張設計 / モジュール配置_
  - _Done When: LoginPage.tsx の catch ブロックが 5 行以下に短縮、`translateLoginError` の import が追加、`console.error` が出力される_

- [x] 4. `LoginPage.test.tsx` に診断出力・新分岐のテストを追加
  - `frontend/src/routing/LoginPage.test.tsx` を改修
  - 既存 3 テスト（成功 / NotAuthorized 表示 / NEW_PASSWORD_REQUIRED 遷移）は本文不変
  - 追加テスト：
    - `UserLambdaValidationException` reject 時に画面に「（コード: UserLambdaValidationException）」が併記される
    - `MissingAuthConfigError` reject 時に「認証設定が未構成です。管理者に連絡してください。」が表示される
    - reject 時に `console.error` が spy で 1 回呼ばれ、引数 object に `code` と `message` を含む
  - _Requirements: 2.1, 2.2, 2.3_
  - _Design: 統合テスト（LoginPage）_
  - _Done When: `vitest --run src/routing/LoginPage.test.tsx` が全件 green、既存テストが壊れていない_

- [x] 5. Frontend テストスイート全実行と Lint / Type チェック（本 spec 変更範囲）
  - `frontend/` で `npm run test`（vitest --run）全件実行、`npm run lint`、`npm run typecheck` を実行
  - 本 spec で追加・変更したファイル（`loginErrors.ts` / `loginErrors.test.ts` / `LoginPage.tsx` / `LoginPage.test.tsx`）に起因する新規エラーが 0 であることを確認
  - 既存の型エラー・Lint 警告（例：`EmployeeFormPage.test.tsx` の TS2352 / TS2493）は本 spec スコープ外として存置し、完了報告に明記する
  - _Requirements: 全件（コード品質担保）_
  - _Done When: 本 spec 追加テスト（`loginErrors.test.ts` 17 件 + `LoginPage.test.tsx` 10 件）が全件 green、フル `vitest --run` で新規失敗が 0、Lint 新規警告が 0、typecheck の新規エラーが 0（既存エラーはそのまま）_

- [x] 6. dev 環境への SPA ビルド + デプロイ（運用作業、AWS 認証情報必須）
  - `frontend/` で以下を実行：
    - `npm ci`
    - `npm run test`（Task 5 の後追い確認）
    - `npm run build`
  - `aws s3 sync frontend/dist/ s3://safety-confirmation-spa-dev-214046906694-ap-northeast-1/ --delete --profile AWS-security-check --region ap-northeast-1`
  - `aws cloudfront create-invalidation --distribution-id EAXOBS3AIJQHH --paths "/*" --profile AWS-security-check`
  - invalidation の Status が `Completed` になるまで待機（通常数分）
  - _Requirements: 1.1〜1.5, 2.1〜2.5（実機での効果発現）_
  - _Design: デプロイ手順_
  - _Done When: CloudFront 経由でアクセスしたときに新バンドル（Task 3 の `console.error` が出る版）が配信される。ブラウザ DevTools の Network で `main.[hash].js` が変更されたことを確認_

- [ ] 7. `FORCE_CHANGE_PASSWORD` 状態のテストユーザー作成 + 手順書 4.2 実機再現（CLI 側完了、ブラウザ検証は残作業）
  - 新規テストユーザーを作成（既存 tomita は `--permanent` 済みのため使用不可）：
    ```
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
  - `describe-user-pool-user` で `UserStatus=FORCE_CHANGE_PASSWORD` を確認
  - ハードリロードした Admin_Console で `issue3-verify@example.com` + `TempPass!2026` を投入
  - Requirement 3.1 の (a) (b) (c) 3 点のスクリーンショット取得
  - _Requirements: 1.1〜1.5, 3.1_
  - _Done When: 3 点のスクリーンショットが取得され、`/new-password` 遷移および新パスワード設定 → `/` 遷移が動作していることが視認できる_

- [ ] 8. `CONFIRMED` 状態および誤パスワード時の追加検証
  - `CONFIRMED` 状態のユーザー（`tomita@g-wise.co.jp`）でログインし、`/` 直接遷移することを確認
  - 誤パスワードで「メールアドレスまたはパスワードが正しくありません。」が表示されることを確認
  - F12 Console で `Login failed: { code: 'NotAuthorizedException', ... }` が出力されることを確認
  - _Requirements: 2.1, 2.4_
  - _Done When: `CONFIRMED` フローが不変、`NotAuthorizedException` メッセージと console 出力が確認できる_

- [ ] 9. 実機検証エビデンスを `docs/notes/fix-initial-login-flow-verification.md` に記録（テンプレ + CLI 部完了、実機記入枠はユーザー実施）
  - Task 7, 8 のスクリーンショットへの相対リンク or 貼付
  - 実行日時、環境、ユーザー、確認事項、Cognito コンソール側スクリーンショット（`UserStatus` 表示）
  - 再発時の切り分けフロー（PostAuth Trigger 状態確認 → `console.error` の code 確認 → Cognito ユーザーステータス確認）
  - _Requirements: 3.1, 3.2, 3.3_
  - _Done When: `docs/notes/fix-initial-login-flow-verification.md` が Markdown diagnostics 0 issues で作成され、本 tasks.md および `.kiro/specs/safety-confirmation-system/tasks.md` 15.6 系タスクから参照可能_

- [ ] 10. GitHub Issue #3 のクローズ（進捗コメント投稿済み `#issuecomment-4933459002`、Close はユーザー実機検証後）
  - Issue #3 に完了コメントを投稿：本 spec のパス、Task 7 のエビデンスファイルパス、変更ファイル一覧、テスト結果を記載
  - Issue #3 を Close
  - _Requirements: 全件（成果物受渡）_
  - _Done When: Issue #3 が `Closed` 状態、コメントに本 spec と検証エビデンスへのリンクが含まれる_

## タスクグルーピング

- Wave 1（コード修正 + 単体テスト、ローカル完結）：Task 1〜5
- Wave 2（実機デプロイ + 検証、AWS 権限必須）：Task 6〜8
- Wave 3（エビデンス記録 + Issue クローズ）：Task 9〜10

## 完了条件（本 spec 全体）

- Task 1〜5 が完了し、`frontend/` の全テストが green で、Lint / Type 0 エラー
- Task 6〜8 が完了し、実機で手順書 4.2 が期待動作通りに遷移する
- Task 9 が完了し、エビデンスが `docs/notes/` に集約されている
- Task 10 が完了し、Issue #3 が Closed

- [x] 11. issue #3 再修正：未分類経路でも識別子を必ず併記（実機報告「（コード: XXX）」が併記されない事象への対応）
  - `frontend/src/routing/loginErrors.ts` に `extractErrorIdentifier(err)` 純粋関数を追加：`err.code` / `err.__type` / `err.name` / `err.constructor.name` / `typeof err` の優先順位で識別子抽出
  - `translateLoginError` の未分類経路（既存 `return GENERIC_PREFIX`）を `return \`${GENERIC_PREFIX}（コード: ${extractErrorIdentifier(err)}）\`` に変更
  - `LoginPage.tsx` の `console.error` に生 err オブジェクトを第3引数として追加、DevTools でオブジェクト展開可能に
  - `loginErrors.test.ts` に `extractErrorIdentifier` 単体テスト 10 件 + 未分類経路のメッセージ更新 7 件を追加
  - `LoginPage.test.tsx` の `console.error` アサーションを第3引数対応に更新、生 SDK エラー風オブジェクト（`{ code: '...' }`）reject 時のケースを追加
  - _Requirements: 2.2, 2.5（再強化）_
  - _Done When: 対象テスト 40 件 all green、build 成功、`extractErrorIdentifier` が全ての未分類 err パターンに対して非空文字列を返す_

- [x] 12. 再デプロイ + 実機再検証依頼（本 spec 内 2 回目のデプロイ）
  - build 成功、新バンドル `index-B8hmR_4j.js`
  - `aws s3 sync` 完了（旧 `index-DXE-hZnW.js` 削除、新バンドルアップロード）
  - CloudFront invalidation `I6OVJXMQ1QDU9ZTR41UPDYGUEG` 発行（Status: InProgress → 数分で Completed）
  - Issue #3 に再修正完了コメント投稿（ハードリロード後の再検証と Console スクリーンショット取得依頼）
  - _Requirements: 1.1〜1.5, 2.1〜2.5（実機での効果発現、2 回目）_
  - _Done When: 新バンドル配信、invalidation Completed、Issue #3 に再検証依頼コメント投稿済み_

- [x] 13. issue #3 真因対応：DataCloneError の解消（history.state の関数伝播回避）
  - 実機で判明した真因：`LoginPage` → `NewPasswordPage` の `navigate('/new-password', { state: { challenge } })` で、React Router が history.state に `structuredClone` する際、challenge オブジェクトが持つ `complete` 関数を clone できず `DataCloneError` を投げていた
  - 対応：`frontend/src/auth/authChallengeStore.ts` を新設（モジュールスコープの一時ストア、`setPendingChallenge` / `consumePendingChallenge` / `clearPendingChallenge`）
  - `LoginPage.tsx`：`navigate('/new-password', { state: { challenge } })` を `setPendingChallenge(result); navigate('/new-password')` に変更
  - `NewPasswordPage.tsx`：`location.state.challenge` を `consumePendingChallenge()` に変更（`useMemo` で初回マウント時 1 回のみ consume、リロード時は null → `/login` へ replace）
  - テスト：`authChallengeStore.test.ts` 新設 6 件、`NewPasswordPage.test.tsx` 全面改修（state 経由 → store 経由に更新）
  - _Requirements: 1.1〜1.5（DataCloneError による遷移失敗の解消）_
  - _Done When: `NEW_PASSWORD_REQUIRED` challenge 遷移で DataCloneError が発生しない、テスト 357 件 all green_

- [x] 14. 再々デプロイ + Issue #3 コメント投稿（本 spec 内 3 回目のデプロイ）
  - 新バンドル：`index-4yw9v3HS.js`（`authChallengeStore.ts` を含む、144 modules）
  - `aws s3 sync` 完了、CloudFront invalidation `IEVU5LGW101US6VC8LQWQ7ZAHF` 発行
  - Issue #3 に真因（DataCloneError）+ 修正内容 + ユーザーへの再検証依頼を投稿
  - _Requirements: 1.1〜1.5（実機効果発現、3 回目）_
  - _Done When: 新バンドル配信、invalidation Completed、Issue コメント投稿済み_

- [x] 15. 新パスワード設定時の Cognito `NotAuthorizedException` UX 対応
  - 実機報告：`/new-password` 遷移までは成功、しかし新パスワードに一時パスワードと同じ値（`TempPass!2026`）を入力すると `completeNewPasswordChallenge` が `NotAuthorizedException` を返し「セッションが無効になりました。再度ログインしてください。」表示
  - 真因仮説：Cognito は「新パスワード = 一時パスワード」を拒否する仕様。この場合 SRP セッションも無効化される
  - `NewPasswordPage.tsx` の UI に「※ 一時パスワードとは異なる値を設定してください」の注意書きを追加
  - `NewPasswordPage.tsx` の `translateCompleteError` の `NotAuthorizedException` メッセージを具体化：`(1) 新パスワードが一時パスワードと同じ / (2) SRP 認証セッション期限切れ / (3) 試行上限到達` の 3 原因候補と復旧手順を提示
  - 未知コードでも「（コード: XXX）」併記に統一（`LoginPage` と同じ診断容易化）
  - テスト：`NewPasswordPage.test.tsx` に 3 件追加（NotAuthorized メッセージ / 未知コード / UI 注意書き）
  - _Requirements: 1.4, 2.4（診断容易化）_
  - _Done When: `NewPasswordPage.test.tsx` 11 件 all green、UI 注意書き表示、NotAuthorized 時に具体的原因候補が UI に露出_

- [x] 16. 4 回目のデプロイ
  - 新バンドル：`index-CaFRSxz2.js`
  - CloudFront invalidation：`I5PGYMSNZBL1FMENVUEHL3UXRN`
  - Issue #3 に 4 回目コメント投稿：即対応（別パスワード試行）+ UX 改善内容
  - _Done When: 新バンドル配信、invalidation Completed、Issue コメント投稿済み_

- [x] 17. SRP challenge Session 1 回制限の UI 対応（Kiro 5 巡目、仮説訂正）
  - 実機報告：`TempPass!202607`（一時と異なる値）でも `NotAuthorizedException`
  - 訂正真因：Cognito の SRP challenge Session は 1 回限り。前回失敗（`TempPass!2026`）で Session 消費、次回以降の `challenge.complete()` は必ず失敗
  - 前回のメッセージ「新パスワード = 一時パスワード拒否」は誤り、実機で反証
  - 対応：`NewPasswordPage.tsx` に `sessionInvalid` state 追加、`NotAuthorizedException` 検知時：
    - `clearPendingChallenge()` で store も安全のためクリア
    - `sessionInvalid = true` で入力欄を disable
    - 「パスワードを設定」ボタンを「ログイン画面に戻る」ボタンに置換
    - エラー文言を「認証セッションが無効化されました。同じセッションでは再試行できない...」に更新
  - UI 注意書きに「認証セッションは 1 回のみ有効」を追加
  - テスト：`NewPasswordPage.test.tsx` に 2 件追加（合計 12 件 all green、NotAuthorized 動線 + 再ログインボタン遷移）
  - _Requirements: 1.4（再試行不能ケースの明示動線）_
  - _Done When: NotAuthorized 検知時にフォーム再送信不可、再ログインボタンから `/login` 遷移、テスト 12 件 all green_

- [x] 18. 5 巡目デプロイ
  - 新バンドル：`index-B5ShzSMP.js`
  - CloudFront invalidation：`I5IP0MVUSU57BM8SQF1EYBWZZ3`
  - Issue #3 に真因訂正コメント投稿
  - _Done When: 新バンドル配信、invalidation Completed、Issue コメント投稿済み_

- [x] 19. `completeNewPasswordChallenge` の userAttributes 渡し方修正（Kiro 6 巡目、真因訂正）
  - 実機再現：`TempPass!202607`（一時と異なる値、SRP セッション再取得後の 1 回目試行）でも `NotAuthorizedException`
  - 5 巡目の仮説「SRP セッション消費」は反証（ハードリロード + 再ログイン後のクリーンセッションでも失敗）
  - AWS CLI で `admin-get-user` 実行結果：`UserStatus: FORCE_CHANGE_PASSWORD`、`UserLastModifiedDate` = 作成時刻。つまり **Cognito 側では password 更新が一切成立していない**
  - 訂正真因（仮説）：`ε-2 修正` で `completeNewPasswordChallenge(newPassword, attrs, ...)` の `attrs` に `sub`（immutable）/ `email`（変更不要）/ `name` を含めて渡していた。Amplify の "SDK 慣例" として広く紹介されているが、Cognito は immutable 属性を含む更新試行を `NotAuthorizedException` として拒否する可能性
  - 対応：`attrs` を空 `{}` に変更（SPA は初回パスワード変更時に追加属性入力を要求しない仕様のため、更新すべき属性は無い）
  - `cognitoAuthProvider.ts` の該当 3 行修正 + テストアサーション更新（`passedAttrs.name === 'A'` → `passedAttrs === {}`）
  - App Client 側の確認：`WriteAttributes: null`（デフォルト、全書き込み可）→ SPA 側 attrs 送信の制限ではないと判明
  - _Requirements: 1.1〜1.5（真の解消）_
  - _Done When: `cognitoAuthProvider.test.ts` 12 件 all green、フル 361 件 all green、実機で `/new-password` → 別パスワード入力 → ダッシュボード遷移が成立、`admin-get-user` で `UserStatus: CONFIRMED` に更新される_

- [x] 20. 6 巡目デプロイ
  - 新バンドル：`index-_1ocmxGG.js`
  - CloudFront invalidation：`I2YEOW5TI0VQAQNTVWPY38QRT2`
  - Issue #3 に真因訂正 + 追加調査依頼コメント投稿（F12 Network タブでの Cognito Response body 確認）
  - _Done When: 新バンドル配信、invalidation Completed、Issue コメント投稿済み_

- [x] 21. 真因確定：CLI 直接検証で `Cannot modify an already provided email` を露出（Kiro 7 巡目、別視点）
  - ユーザー指示「別視点で見直せ」を受け、SPA / SDK を離れて AWS CLI `admin-respond-to-auth-challenge` を直接実行
  - `safety-confirmation-cli-test-dev` App Client（`ADMIN_USER_PASSWORD_AUTH` 許可）を活用
  - 検証 1（`attrs = {}`、6 巡目バンドル相当）：`AuthenticationResult` 返り成功、`UserStatus: CONFIRMED` 到達
  - 検証 2（`attrs = { email, name }`、5 巡目バンドル相当）：Cognito が `NotAuthorizedException: Cannot modify an already provided email` を返却
  - **真因確定**：Amplify の「SDK 慣例」で `userAttributes` を渡すと、Cognito が「既に提供済み email の変更試行」として拒否。ε-2 修正の設計採用が失敗の根本原因
  - 現状：6 巡目バンドル `index-_1ocmxGG.js` は既にこの修正を反映済み、CloudFront で配信中
  - _Requirements: 1.1〜1.5（真因確定）_
  - _Done When: Cognito 側の明示的エラーメッセージが取得され、6 巡目バンドルの修正方向が客観的に検証された_
