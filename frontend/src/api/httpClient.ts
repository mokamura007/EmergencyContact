/**
 * 認証付き HTTP クライアント（インターセプタ）。
 *
 * 対応要件（tasks.md 10.2）:
 *   - ID トークンを `Authorization: Bearer <ID_TOKEN>` ヘッダに付与する。
 *   - リフレッシュトークンによる自動更新、期限切れ時は再ログインへ誘導。
 *     → `getCurrentSession` 内（CognitoAuthProvider 側）で SDK が
 *        自動リフレッシュする。リフレッシュが失敗した場合は
 *        `SessionExpiredError` を伝播し、`sessionExpiredEvent` 経由で
 *        再ログイン誘導側（UI 層）に通知する。
 *   - 一般社員向け画面は実装しないため、本クライアントは管理者専用 API
 *     呼出のみを想定する（特別な分岐は無い）。
 *
 * 設計判断：
 *   - `fetch` をラップした自前インターセプタとする（axios を追加しない）。
 *     依存最小化と DRY のため、ライブラリは増やさない。
 *   - レスポンス 401 は「サーバーが ID トークンを拒否した」状態であり、
 *     再ログイン誘導が必要なので `notifySessionExpired` を発火する。
 *     ただし HTTP レスポンス自体は呼出側に返す（19原則 (b)：エラーを
 *     握り潰さずそのまま伝える方針。401 のハンドリングは呼出側が選択する）。
 */

import { getAuthProvider } from '../auth';
import { SessionExpiredError } from '../auth/errors';
import { notifySessionExpired } from '../auth/sessionExpiredEvent';
import type { AuthSessionProvider } from '../auth/types';
import { getEnv } from '../config/env';

export interface AuthFetchOptions {
  /** 認証プロバイダ。未指定なら `getAuthProvider()` のシングルトンを使う。 */
  readonly authProvider?: AuthSessionProvider;
  /**
   * `fetch` 実装。テスト時に差し替えるため。
   *
   * 標準 `fetch` は `(input: RequestInfo | URL, init?) => ...` だが、本
   * インターセプタは `Request` オブジェクト経由の Authorization 上書きを
   * サポートせず `string | URL` のみ受け付けるため、型もそれに合わせる。
   */
  readonly fetchImpl?: (input: string | URL, init?: RequestInit) => Promise<Response>;
  /** `notifySessionExpired` の差し替え用。テスト時のみ使う。 */
  readonly onSessionExpired?: () => void;
}

/**
 * `authFetch` 関数のシグネチャ。標準 `fetch` と互換だが、`input` は
 * 文字列または `URL` を受ける（`Request` オブジェクト経由の Authorization
 * ヘッダ上書きは複雑になるため非対応とする）。
 */
export type AuthFetch = (input: string | URL, init?: RequestInit) => Promise<Response>;

/**
 * 認証付き fetch を生成するファクトリ。
 * DI を明示する（`authProvider` / `fetchImpl` / `onSessionExpired`）。
 */
export function createAuthFetch(options: AuthFetchOptions = {}): AuthFetch {
  const authProvider = options.authProvider ?? getAuthProvider();
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const onSessionExpired = options.onSessionExpired ?? notifySessionExpired;

  return async (input, init) => {
    let idToken: string;
    try {
      const session = await authProvider.getCurrentSession();
      if (session === null) {
        // セッション失効を catch 側に統一処理させるため SessionExpiredError として伝搬。
        throw new SessionExpiredError('No active session. Login required.');
      }
      idToken = session.idToken;
    } catch (err) {
      // SessionExpiredError は再ログイン誘導 + 上位伝播。
      if (err instanceof SessionExpiredError) {
        onSessionExpired();
        throw err;
      }
      // それ以外の予期しないエラーはそのまま伝える（19原則 (b)）。
      throw err;
    }

    const headers = new Headers(init?.headers);
    headers.set('Authorization', `Bearer ${idToken}`);
    if (!headers.has('Accept')) {
      headers.set('Accept', 'application/json');
    }

    const response = await fetchImpl(input, { ...init, headers });

    if (response.status === 401) {
      // サーバー側で ID トークンが拒否された。再ログインへ誘導。
      // Response 自体は呼出側に返し、ハンドリングを委ねる。
      onSessionExpired();
    }
    return response;
  };
}

/**
 * API Gateway ベース URL を環境変数から組み立てる。
 * 末尾スラッシュは付与しない（呼出側が `/path` で連結する想定）。
 */
export function getApiBaseUrl(): string {
  const env = getEnv();
  if (!env.apiBaseUrl) {
    throw new Error('API base URL is missing. Set VITE_API_BASE_URL before building the SPA.');
  }
  return env.apiBaseUrl.replace(/\/+$/, '');
}
