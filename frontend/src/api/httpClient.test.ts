/**
 * `createAuthFetch` HTTP インターセプタのテスト。
 *
 * 検証観点：
 *   - 認証セッションから取得した ID トークンを `Authorization: Bearer <token>`
 *     として送信ヘッダに付与する。
 *   - リフレッシュは `AuthSessionProvider.getCurrentSession` 内（=Cognito SDK）が
 *     行うため、ここでは「`getCurrentSession` が新しい session を返したら
 *     その idToken が使われる」ことを fake で確認する。
 *   - セッション失効（`getCurrentSession` が `null` / `SessionExpiredError` を
 *     投げる）と、サーバー側 401 応答の双方で `onSessionExpired` が呼ばれる。
 */

import { describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';

import { SessionExpiredError } from '../auth/errors';
import type { AuthSessionProvider, TokenSet } from '../auth/types';
import { createAuthFetch } from './httpClient';

type FetchSignature = (input: string | URL, init?: RequestInit) => Promise<Response>;
type FetchMock = Mock<FetchSignature>;

function makeAuthProvider(getSessionImpl: () => Promise<TokenSet | null>): AuthSessionProvider {
  return {
    signIn: vi.fn(),
    getCurrentSession: vi.fn(getSessionImpl),
    signOut: vi.fn(),
  };
}

function makeResponse(status = 200, body: unknown = { ok: true }): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function makeFetchMock(response: Response): FetchMock {
  return vi.fn<FetchSignature>(() => Promise.resolve(response));
}

/** mock.calls から N 番目の呼出を取り出す（厳格 index アクセスのヘルパ）。 */
function getCallArgs(
  fetchMock: FetchMock,
  index: number,
): { url: string | URL; init: RequestInit | undefined } {
  const call = fetchMock.mock.calls[index];
  if (!call) {
    throw new Error(`fetch was not called ${String(index + 1)} time(s)`);
  }
  return { url: call[0], init: call[1] };
}

describe('createAuthFetch', () => {
  it('attaches Authorization: Bearer <idToken> using the current session', async () => {
    const auth = makeAuthProvider(() =>
      Promise.resolve({
        idToken: 'current-id-token',
        accessToken: 'current-access-token',
        expiresAtEpochSeconds: Math.floor(Date.now() / 1000) + 3600,
      }),
    );
    const fetchImpl = makeFetchMock(makeResponse(200));
    const onExpired = vi.fn();

    const authFetch = createAuthFetch({
      authProvider: auth,
      fetchImpl,
      onSessionExpired: onExpired,
    });

    await authFetch('https://api.example.com/cycles');

    expect(fetchImpl).toHaveBeenCalledOnce();
    const { url, init } = getCallArgs(fetchImpl, 0);
    expect(url).toBe('https://api.example.com/cycles');
    const headers = new Headers(init?.headers);
    expect(headers.get('Authorization')).toBe('Bearer current-id-token');
    expect(headers.get('Accept')).toBe('application/json');
    expect(onExpired).not.toHaveBeenCalled();
  });

  it('uses the refreshed token when the provider auto-refreshes between calls', async () => {
    // 1 回目 / 2 回目で異なる idToken を返すスタブ。`getCurrentSession` 内で
    // SDK が refresh token を使って token を更新したという状況を模す。
    let counter = 0;
    const auth = makeAuthProvider(() => {
      counter += 1;
      return Promise.resolve({
        idToken: counter === 1 ? 'first-id' : 'refreshed-id',
        accessToken: 'a',
        expiresAtEpochSeconds: 1_900_000_000,
      });
    });
    const fetchImpl = makeFetchMock(makeResponse(200));
    const authFetch = createAuthFetch({
      authProvider: auth,
      fetchImpl,
      onSessionExpired: vi.fn(),
    });

    await authFetch('https://api.example.com/cycles');
    await authFetch('https://api.example.com/cycles');

    const firstHeaders = new Headers(getCallArgs(fetchImpl, 0).init?.headers);
    const secondHeaders = new Headers(getCallArgs(fetchImpl, 1).init?.headers);
    expect(firstHeaders.get('Authorization')).toBe('Bearer first-id');
    expect(secondHeaders.get('Authorization')).toBe('Bearer refreshed-id');
  });

  it('notifies session expired and throws SessionExpiredError when no session is available', async () => {
    const auth = makeAuthProvider(() => Promise.resolve(null));
    const fetchImpl = makeFetchMock(makeResponse(200));
    const onExpired = vi.fn();

    const authFetch = createAuthFetch({
      authProvider: auth,
      fetchImpl,
      onSessionExpired: onExpired,
    });

    await expect(authFetch('https://api.example.com/x')).rejects.toBeInstanceOf(
      SessionExpiredError,
    );
    expect(onExpired).toHaveBeenCalledOnce();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('propagates SessionExpiredError from the provider and notifies', async () => {
    const auth = makeAuthProvider(() =>
      Promise.reject(new SessionExpiredError('Refresh token expired')),
    );
    const fetchImpl = makeFetchMock(makeResponse(200));
    const onExpired = vi.fn();

    const authFetch = createAuthFetch({
      authProvider: auth,
      fetchImpl,
      onSessionExpired: onExpired,
    });

    await expect(authFetch('https://api.example.com/x')).rejects.toBeInstanceOf(
      SessionExpiredError,
    );
    expect(onExpired).toHaveBeenCalledOnce();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('does NOT swallow non-auth errors thrown by the provider', async () => {
    const auth = makeAuthProvider(() => Promise.reject(new Error('network down')));
    const fetchImpl = makeFetchMock(makeResponse(200));
    const onExpired = vi.fn();

    const authFetch = createAuthFetch({
      authProvider: auth,
      fetchImpl,
      onSessionExpired: onExpired,
    });

    await expect(authFetch('https://api.example.com/x')).rejects.toThrowError('network down');
    // 通常のネットワークエラーで session expired は通知しない（19原則 (b)）。
    expect(onExpired).not.toHaveBeenCalled();
  });

  it('notifies session expired on HTTP 401 responses and still returns the Response', async () => {
    const auth = makeAuthProvider(() =>
      Promise.resolve({
        idToken: 'token',
        accessToken: 'a',
        expiresAtEpochSeconds: 1_900_000_000,
      }),
    );
    const unauthorized = makeResponse(401, { error: 'Unauthorized' });
    const fetchImpl = makeFetchMock(unauthorized);
    const onExpired = vi.fn();

    const authFetch = createAuthFetch({
      authProvider: auth,
      fetchImpl,
      onSessionExpired: onExpired,
    });

    const res = await authFetch('https://api.example.com/x');
    expect(res.status).toBe(401);
    expect(onExpired).toHaveBeenCalledOnce();
  });

  it('preserves the Accept header set by the caller and the request method/body', async () => {
    const auth = makeAuthProvider(() =>
      Promise.resolve({
        idToken: 't',
        accessToken: 'a',
        expiresAtEpochSeconds: 1_900_000_000,
      }),
    );
    const fetchImpl = makeFetchMock(makeResponse(200));
    const authFetch = createAuthFetch({
      authProvider: auth,
      fetchImpl,
      onSessionExpired: vi.fn(),
    });

    await authFetch('https://api.example.com/cycles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/vnd.example+json' },
      body: JSON.stringify({ mode: 'ALL' }),
    });

    const { init } = getCallArgs(fetchImpl, 0);
    expect(init?.method).toBe('POST');
    expect(init?.body).toBe(JSON.stringify({ mode: 'ALL' }));
    const headers = new Headers(init?.headers);
    expect(headers.get('Authorization')).toBe('Bearer t');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('Accept')).toBe('application/vnd.example+json');
  });
});
