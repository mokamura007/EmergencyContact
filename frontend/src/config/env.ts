/**
 * 環境変数アクセサ。
 *
 * Vite は `import.meta.env.VITE_*` 経由でビルド時に環境変数を埋め込む。
 * 本モジュールはアプリケーションコードから直接 `import.meta.env` を
 * 触らせず、型付きの 1 個の窓口に集約することで DRY 原則（19原則(a)）に従う。
 *
 * フォールバック禁止（19原則(b)）方針：
 *   - 必須値（API base URL / Cognito Pool ID / Client ID）が未設定だった場合は
 *     `getEnv()` の戻り値が空文字を含む状態となり、上位コードは空判定で
 *     fail-fast できる。本モジュール側で勝手にデフォルト値を埋めることはしない。
 *   - `awsRegion` のみは Connect / Cognito の東京リージョン固定（NFR5）に
 *     合わせ `ap-northeast-1` を既定値として返す。
 *
 * 対応する環境変数定義は `.env.example` を参照のこと。
 */

export interface AppEnv {
  readonly apiBaseUrl: string;
  readonly cognitoUserPoolId: string;
  readonly cognitoClientId: string;
  readonly awsRegion: string;
}

export function getEnv(): AppEnv {
  return {
    apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
    cognitoUserPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID ?? '',
    cognitoClientId: import.meta.env.VITE_COGNITO_CLIENT_ID ?? '',
    awsRegion: import.meta.env.VITE_AWS_REGION ?? 'ap-northeast-1',
  };
}
