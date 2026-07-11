/**
 * ログイン失敗時のエラー → 画面表示メッセージ変換（純粋関数）。
 *
 * 対応要件：
 *   - Requirement 2.2 : 未知の Cognito コードは汎用メッセージ + `（コード: XXX）` 併記
 *   - Requirement 2.3 : `MissingAuthConfigError` は専用メッセージ
 *   - Requirement 2.4 : 既知 Cognito コードの個別分岐
 *   - Requirement 2.5 : 予期しない値もフォールバック禁止で情報を残す
 *
 * 設計判断：
 *   - 純粋関数化により `LoginPage` から責務を切り出し、単体テスト容易性と
 *     DRY を両立する（第 19 原則 (a)）。
 *   - `AuthenticationFailedError` に該当しない値であっても、`Error.name`
 *     経由で `NewPasswordRequiredError` を検出できるようにし、ε-2 修正
 *     以前の旧経路（例外方式）が混入した場合の隠れバグを検知する。
 *   - 出力メッセージはすべて日本語直書き（既存 `LoginPage.tsx` と揃える）。
 *     i18n は将来 Phase で導入する。
 */

import { AuthenticationFailedError, MissingAuthConfigError } from '../auth/errors';

const CREDENTIAL_MESSAGE = 'メールアドレスまたはパスワードが正しくありません。';
const GENERIC_PREFIX = 'ログインに失敗しました。時間をおいて再度お試しください。';

/**
 * 任意の err 値から、UI と Console に露出できる識別子を抽出する。
 *
 * 優先順位（Cognito SDK / 独自 Error / 素の値の順で判定）：
 *   1. `err.code`（Cognito 独自コード、`NotAuthorizedException` 等）
 *   2. `err.__type`（Cognito HTTP レスポンスのタイプ、`com.amazonaws...` 等）
 *   3. `err.name`（`Error` 派生の name）
 *   4. `err.constructor.name`（クラス名）
 *   5. `typeof err`（`string` / `number` / `undefined` 等の原始型）
 *
 * 未分類経路でも必ず何らかの識別子を返し、issue #3 のような
 * 「汎用文言のみが出て真因が特定できない」状況を回避する。
 */
export function extractErrorIdentifier(err: unknown): string {
  if (err === null) return 'null';
  if (err === undefined) return 'undefined';
  if (typeof err !== 'object') return typeof err;

  const record = err as Record<string, unknown>;
  if (typeof record.code === 'string' && record.code.length > 0) return record.code;
  if (typeof record.__type === 'string' && record.__type.length > 0) return record.__type;
  if (typeof record.name === 'string' && record.name.length > 0) return record.name;

  const ctor = (err as { constructor?: { name?: unknown } }).constructor;
  if (ctor && typeof ctor.name === 'string' && ctor.name.length > 0) return ctor.name;
  return 'unknown';
}

/**
 * 予期される Cognito エラーコード → 画面表示メッセージのマッピング。
 * 未知コードは default 経路で `GENERIC_PREFIX + `（コード: XXX）`` を返す。
 */
const KNOWN_COGNITO_MESSAGES: Readonly<Record<string, string>> = {
  NotAuthorizedException: CREDENTIAL_MESSAGE,
  UserNotFoundException: CREDENTIAL_MESSAGE,
  PasswordResetRequiredException:
    'パスワードのリセットが必要です。システム管理者にお問い合わせください。',
  UserLambdaValidationException:
    'ログイン後処理でエラーが発生しました。システム管理者にお問い合わせください。（コード: UserLambdaValidationException）',
  TooManyRequestsException:
    '短時間に多くのリクエストが行われました。しばらく待って再度お試しください。',
  LimitExceededException:
    '短時間に多くのリクエストが行われました。しばらく待って再度お試しください。',
  UserNotConfirmedException: 'アカウントが有効化されていません。管理者にお問い合わせください。',
};

/**
 * ログイン処理の `catch` で捕捉した値を画面表示メッセージに変換する。
 *
 * @param err `provider.signIn` から reject された任意の値。
 * @returns ユーザーに表示すべき日本語メッセージ。
 */
export function translateLoginError(err: unknown): string {
  if (err instanceof MissingAuthConfigError) {
    return '認証設定が未構成です。管理者に連絡してください。';
  }

  if (err instanceof AuthenticationFailedError) {
    const known = KNOWN_COGNITO_MESSAGES[err.code];
    if (known !== undefined) {
      return known;
    }
    // 未知コードは診断容易化のため、汎用文言の末尾にコードを併記する。
    return `${GENERIC_PREFIX}（コード: ${err.code}）`;
  }

  // ε-2 修正後は `NewPasswordRequiredError` はスローされない前提だが、
  // 差し替え等で旧経路が混入した場合の隠れバグを検知するため、専用メッセージを返す。
  if (err instanceof Error && err.name === 'NewPasswordRequiredError') {
    return 'システム状態が矛盾しています。管理者にお問い合わせください。';
  }

  // 予期しない値。issue #3 の再修正：汎用文言だけでは真因特定不能となるため、
  // `extractErrorIdentifier` で必ず識別子を UI に併記する。
  // これにより Cognito SDK 生エラー / Error 派生でない値 / cross-realm Error 等の
  // どのケースでも DevTools を開かずに XXX で切り分けが可能となる。
  return `${GENERIC_PREFIX}（コード: ${extractErrorIdentifier(err)}）`;
}
