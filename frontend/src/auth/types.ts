/**
 * 認証セッション抽象。
 *
 * 認証バックエンド（Cognito User Pool）を差し替え可能にするため、
 * SPA の HTTP 層・UI 層は本インターフェースのみに依存する。
 * 単体テストではフェイク実装を注入することで、ネットワーク依存を排し
 * 純粋に挙動だけを検証する。
 */

/**
 * 認証成功時に取得されるトークン集合。
 *
 * - `idToken`: API Gateway の Cognito Authorizer に `Authorization: Bearer`
 *   として提示する ID トークン（要件 1.2 / design.md「Auth_Service」）。
 * - `accessToken`: Cognito 自身のユーザー属性参照等で必要だが、本 SPA では
 *   主に `idToken` を利用する。
 * - `expiresAtEpochSeconds`: ID トークンの絶対有効期限（unix 秒）。
 *   インターセプタ側でクロックスキューを考慮した期限判定に使う。
 */
export interface TokenSet {
  readonly idToken: string;
  readonly accessToken: string;
  readonly expiresAtEpochSeconds: number;
}

/**
 * NEW_PASSWORD_REQUIRED チャレンジ状態。
 *
 * Cognito ユーザープールで `UserStatus=FORCE_CHANGE_PASSWORD` の
 * ユーザーが SRP 認証を行うと、SDK の `newPasswordRequired` コールバック
 * で呼び出される。本 SPA では当該 challenge を例外ではなく型で表現し、
 * `complete(newPassword)` で完了させる（ε-2 修正、15.2a 実機検証）。
 *
 * 状態は本オブジェクトに閉じ込められ、`complete` 内部で同一の
 * CognitoUser インスタンス（クロージャ捕捉）に対して
 * `completeNewPasswordChallenge` を呼ぶ。一度 `complete` が成功すると
 * 同オブジェクトの再利用は未定義（SDK 側がセッションを進めるため）。
 */
export interface NewPasswordRequiredChallenge {
  readonly kind: 'NEW_PASSWORD_REQUIRED';
  /**
   * 新パスワードで challenge を完了させる。成功時に TokenSet を返す。
   * Cognito 側の Required Attributes（name 等）が非空の場合や
   * 新パスワードがパスワードポリシー違反の場合は
   * `AuthenticationFailedError` を投げる（19 原則 (b)：フォールバック禁止）。
   */
  complete(newPassword: string): Promise<TokenSet>;
}

/**
 * `AuthSessionProvider.signIn` の戻り値。
 *
 * - `SUCCESS`: 通常成功（TokenSet を保持）。
 * - `NEW_PASSWORD_REQUIRED`: 初回ログインで新パスワード設定が必要。
 *   UI 層は kind で分岐し、後者の場合は新パスワード入力画面へ遷移する。
 */
export type SignInResult =
  | { readonly kind: 'SUCCESS'; readonly tokens: TokenSet }
  | NewPasswordRequiredChallenge;

/**
 * 認証セッション提供者。
 *
 * - `signIn` : SRP_AUTH によるログイン。資格情報が誤りなら例外を投げる。
 *   フォールバック禁止（19原則 (b)）に従い、内部エラーは原因型を残して
 *   そのまま伝播させる。FORCE_CHANGE_PASSWORD ユーザーの場合は
 *   `NewPasswordRequiredChallenge` を resolve として返す。
 * - `getCurrentSession` : 現在のセッションを返す。ID/Access トークン期限が
 *   切れていてもリフレッシュトークンが有効ならば内部で自動更新を試みる。
 *   有効セッションが存在しない（未ログイン or リフレッシュトークンも失効）
 *   ならば `null` を返す。
 * - `signOut` : ローカル / リモートのセッションを破棄する。
 */
export interface AuthSessionProvider {
  signIn(email: string, password: string): Promise<SignInResult>;
  getCurrentSession(): Promise<TokenSet | null>;
  signOut(): Promise<void>;
}
