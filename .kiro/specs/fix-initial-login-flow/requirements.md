# Requirements Document

## Introduction

本 spec は、`docs/operations/mock-integration-test.md` 手順書 4.2（初回ログイン）で再現する不具合（GitHub Issue #3）の解消を目的とする。

現状（2026-07-10 時点）：`FORCE_CHANGE_PASSWORD` 状態のユーザーで SPA からログインを試みると、SPA は「初回パスワード変更画面」へ遷移せず、汎用エラー「ログインに失敗しました。時間をおいて再度お試しください。」を表示する。手順書は「初回ログインのため自動的に初回パスワード変更画面へ遷移します」を期待動作としており、実装と齟齬がある。

過去経緯（`docs/notes/dev-login-followup.md`）：同一現象は 2026-06-26 に一度発見され、`admin-set-user-password --permanent` および PostAuth Trigger 除去で個別回避後、以下 2 系統の対処が進行：

- (i) PostAuth Lambda import エラー：2026-06-27 に修正・再アタッチ完了（`docs/notes/15-20-postauth-import-fix.md`）
- (ii) SPA 側 SRP 実装の再検証：ε-2 修正で `NewPasswordRequiredError` 例外方式から `NEW_PASSWORD_REQUIRED` 型結果方式にリファクタリング済（`frontend/src/auth/cognitoAuthProvider.ts`）。ただしコード上 fix はされたが、**実機での再現・検証が未実施**（tasks.md 15.6 の受入テスト範囲）

本 spec は上記 (ii) の残課題を独立タスクとして扱い、次の 3 点を達成する。

1. `FORCE_CHANGE_PASSWORD` 状態ユーザーの実機ログインが手順書 4.2 の期待動作通りに遷移する。
2. 実機で失敗が再発した際の原因特定容易化（エラーコードが運用者・利用者に見える）。
3. 手順書 4.2 の再現手順が実機で PASS したことをエビデンスとして記録する。

本 spec は既存 `.kiro/specs/safety-confirmation-system/` の Requirement 1（管理者認証）に対する追加要件として位置付ける。既存 Acceptance Criteria の書き換えは行わず、本 spec 内に補完 AC を新規定義する。

## Glossary

- **FORCE_CHANGE_PASSWORD**: Cognito User Pool のユーザーステータス。`admin-create-user` で作成された直後のユーザーが持つ状態で、初回ログイン時に新パスワードへの変更を強制される。
- **NEW_PASSWORD_REQUIRED**: amazon-cognito-identity-js の `authenticateUser` 呼出において、`FORCE_CHANGE_PASSWORD` 状態のユーザーが SRP 認証を試みた際に SDK が発火するコールバック名。SPA では ε-2 修正により、これを例外ではなく `SignInResult` の 1 バリアントとして表現する。
- **ε-2 修正**: 2026-06-27 に実施された SPA 認証層のリファクタリング。`newPasswordRequired` を例外（`NewPasswordRequiredError`）から型結果（`SignInResult.kind = 'NEW_PASSWORD_REQUIRED'`）に変更し、UI フローで complete まで進めるようにした変更を指す。
- **診断容易化 (diagnosability)**: 実機での失敗時に、運用者が F12 開発者ツール等を介さずに、または介した場合でも即座に、Cognito から返された `__type` / `code` の値を特定できる状態を指す。
- **手順書 4.2**: `docs/operations/mock-integration-test.md` §4.2「初回ログイン」節。テスト用ユーザー `integration-test-admin@example.com` を一時パスワード `TempPass!2026` でログインし、初回パスワード変更画面へ遷移することを期待する手順。

## Requirements

### Requirement 1: 初回パスワード変更フローの実機動作

**User Story:** テスト実施者として、手順書 4.2 に従い一時パスワードでログインしたときに、初回パスワード変更画面へ自動遷移させたい。それにより、手順書 4.3 以降のテストを継続できる。

#### Acceptance Criteria

1. WHEN テスト実施者が Admin_Console のログイン画面で `FORCE_CHANGE_PASSWORD` 状態のユーザー（例：`integration-test-admin@example.com`、パスワード `TempPass!2026`）の正しい認証情報を入力し「ログイン」を押下する, THE Admin_Console SHALL SPA 内ルーティングで `/new-password` へ遷移する。
2. WHEN Admin_Console が `/new-password` に遷移する, THE Admin_Console SHALL 直前の SRP 認証で得た `NewPasswordRequiredChallenge` を `history.state.challenge` として保持し、`NewPasswordPage` に対して challenge 有効性を検証可能にする。
3. WHEN テスト実施者が `/new-password` で新パスワード（Cognito パスワードポリシー準拠）と確認パスワード（同一値）を入力し「パスワードを設定」を押下する, THE Admin_Console SHALL `NewPasswordRequiredChallenge.complete(newPassword)` を呼び、resolve 後にダッシュボード `/` へ replace 遷移する。
4. IF `NewPasswordRequiredChallenge.complete` が Cognito パスワードポリシー違反等で `AuthenticationFailedError` を投げる, THEN THE Admin_Console SHALL 対応するメッセージ（`InvalidPasswordException` / `InvalidParameterException` / `NotAuthorizedException` / `RequiredAttributesUnsupported` 個別分岐、それ以外は汎用）を表示し、フォームを再入力可能にする。
5. THE Admin_Console SHALL 手順書 4.2 の状況下（`FORCE_CHANGE_PASSWORD` ユーザー + 正しい一時パスワード）において、`AuthenticationFailedError` を UI 上に投げ出してはならない（`newPasswordRequired` は例外ではなく `SignInResult` の 1 バリアントとして扱われる）。

### Requirement 2: ログイン失敗時の診断容易化

**User Story:** 運用者として、ログイン失敗が発生した際に Cognito から返された原因コードを即座に特定したい。それにより、`FORCE_CHANGE_PASSWORD` 誤検出、PostAuth Lambda 障害再発、設定不整合などの根本原因を短時間で切り分けられる。

#### Acceptance Criteria

1. WHEN `LoginPage` の `catch` ブロックで `AuthenticationFailedError` を捕捉する, THE Admin_Console SHALL 当該 error の `code` を必ず `console.error` へ出力し、開発者ツールから確認可能にする（本番でも出力する。ID / パスワード等の入力値は含めない）。
2. WHEN `LoginPage` の `catch` ブロックで `AuthenticationFailedError` を捕捉しかつ既知の code（`NotAuthorizedException` / `UserNotFoundException` / `PasswordResetRequiredException`）に該当しない, THE Admin_Console SHALL 画面表示メッセージに当該 code を末尾に括弧付きで併記する（例：「ログインに失敗しました。時間をおいて再度お試しください。（コード: UserLambdaValidationException）」）。
3. WHEN `LoginPage` の `catch` ブロックで `MissingAuthConfigError` を捕捉する, THE Admin_Console SHALL `AuthenticationFailedError` とは別の専用メッセージ（例：「認証設定が未構成です。管理者に連絡してください。」）を表示する。
4. THE Admin_Console SHALL 以下の Cognito エラーコードに対して個別の日本語メッセージを分岐する：
   - `NotAuthorizedException`：メールアドレスまたはパスワードが正しくありません。
   - `UserNotFoundException`：メールアドレスまたはパスワードが正しくありません。
   - `PasswordResetRequiredException`：パスワードのリセットが必要です。システム管理者にお問い合わせください。
   - `UserLambdaValidationException`：ログイン後処理でエラーが発生しました。システム管理者にお問い合わせください。（コード: UserLambdaValidationException）
   - `TooManyRequestsException` / `LimitExceededException`：短時間に多くのリクエストが行われました。しばらく待って再度お試しください。
   - `UserNotConfirmedException`：アカウントが有効化されていません。管理者にお問い合わせください。
5. IF 予期しない Cognito エラーコードが返る, THEN THE Admin_Console SHALL Requirement 2.2 の汎用メッセージ + コード併記形式で表示する（フォールバック禁止：19 原則(b)）。
6. IF catch に渡された値が `AuthenticationFailedError` にも `MissingAuthConfigError` にも該当しない（生 Cognito SDK エラー / Error 派生でない値 / cross-realm Error 等）, THEN THE Admin_Console SHALL `err.code` → `err.__type` → `err.name` → `err.constructor.name` → `typeof err` の優先順位で識別子を抽出し、Requirement 2.2 と同じ汎用メッセージ + コード併記形式で必ず表示する（issue #3 実機報告：汎用文言のみが出て真因が特定できない事象への対応）。
7. WHEN `LoginPage` の `catch` ブロックで任意のエラーを捕捉する, THE Admin_Console SHALL `console.error('Login failed:', ...)` の第3引数として生 err オブジェクトを渡し、DevTools でオブジェクト展開して SDK 内部プロパティ（`__type` / `statusCode` / `retryable` 等）を目視確認可能にする。

### Requirement 3: 手順書 4.2 の実機再現エビデンス

**User Story:** 運用者として、issue #3 が実機で解消したことを再現手順とともにエビデンス化したい。それにより、同一現象の再発時に切り分け起点となる基準ケースを持てる。

#### Acceptance Criteria

1. WHEN Requirement 1 の実装完了後に手順書 4.2 を実機で再実行する, THE 記録者 SHALL 以下 3 点をスクリーンショット or 画面ダンプで残す：(a) ログイン画面での入力状態、(b) 初回パスワード変更画面への遷移、(c) 新パスワード設定成功後のダッシュボード表示。
2. IF Requirement 1 の実装完了後にも実機で不具合が再発する, THEN THE 記録者 SHALL Requirement 2.1 で `console.error` に出力される Cognito エラーコードを記録し、原因（Cognito 設定 / PostAuth Lambda / 環境変数 / ビルドアーティファクト鮮度）の切り分けを本 spec の tasks.md に追記する。
3. THE 記録者 SHALL 実機検証結果を `docs/notes/fix-initial-login-flow-verification.md`（新規作成）に集約し、本 spec の tasks.md から相互参照する。

## スコープ外

以下は本 spec の対象外とする（別 spec / 別タスクで扱う）：

- 実 Amazon Connect 発信を伴う Acceptance Criteria の踏破（既存 `.kiro/specs/safety-confirmation-system/` tasks.md 15.6 が担当）。
- Cognito ユーザープールのパスワードポリシー変更、MFA 有効化、SSO 導入等の認証要件拡張。
- 一般社員向けログイン画面の実装（Requirement 1.9 で明示的に不要）。
- 初回パスワード変更以外の Cognito challenge（`SMS_MFA` / `SOFTWARE_TOKEN_MFA` / `SELECT_MFA_TYPE` 等）への対応。
- CloudFront への SPA デプロイ自動化（本 spec は手動 `npm run build` + `aws s3 sync` + invalidation を前提とする）。
