/**
 * `CognitoAuthProvider` の挙動テスト。
 *
 * Cognito SDK は `localStorage` 経由でセッションを永続化するため、
 * テストではコールバックレベルで SDK 挙動を fake する。
 * - `CognitoUserPool` は `userPoolFactory` DI で fake に差し替える。
 * - `CognitoUser` / `AuthenticationDetails` は `vi.mock` でモジュール
 *   全体をモックし、`signIn` の経路で生成される fake を制御する。
 */

import { describe, expect, it, vi } from 'vitest';
import type { CognitoUserPool, CognitoUserSession } from 'amazon-cognito-identity-js';

import { CognitoAuthProvider } from './cognitoAuthProvider';
import { AuthenticationFailedError, SessionExpiredError } from './errors';

interface FakeSession {
  isValid: () => boolean;
  getIdToken: () => { getJwtToken: () => string; getExpiration: () => number };
  getAccessToken: () => { getJwtToken: () => string; getExpiration: () => number };
}

function makeFakeSession(opts: {
  idJwt?: string;
  accessJwt?: string;
  expiresAt?: number;
  valid?: boolean;
}): FakeSession {
  const idJwt = opts.idJwt ?? 'id.jwt.token';
  const accessJwt = opts.accessJwt ?? 'access.jwt.token';
  const expiresAt = opts.expiresAt ?? Math.floor(Date.now() / 1000) + 3600;
  const valid = opts.valid ?? true;
  return {
    isValid: () => valid,
    getIdToken: () => ({ getJwtToken: () => idJwt, getExpiration: () => expiresAt }),
    getAccessToken: () => ({ getJwtToken: () => accessJwt, getExpiration: () => expiresAt }),
  };
}

interface FakeCognitoUser {
  authenticateUser: ReturnType<typeof vi.fn>;
  getSession: ReturnType<typeof vi.fn>;
  signOut: ReturnType<typeof vi.fn>;
  completeNewPasswordChallenge?: ReturnType<typeof vi.fn>;
}

// -- `amazon-cognito-identity-js` 全体を module-mock --------------------
//
// `CognitoUser` 経路だけは constructor で fake インスタンスを返したいので
// vi.mock でモジュール全体を hijack する。`CognitoUserPool` は
// `CognitoAuthProvider` の DI（userPoolFactory）経由で fake を渡せるので、
// ここでは constructor を素通しさせるだけで OK。

const fakeUserBehavior: { current: FakeCognitoUser } = {
  current: {
    authenticateUser: vi.fn(),
    getSession: vi.fn(),
    signOut: vi.fn(),
  },
};

vi.mock('amazon-cognito-identity-js', () => {
  return {
    AuthenticationDetails: vi.fn().mockImplementation((data: unknown) => ({ _data: data })),
    CognitoUser: vi.fn().mockImplementation(() => fakeUserBehavior.current),
    // 本ファイルでは DI 経由で fake pool を使うため、constructor 本体は使われない。
    CognitoUserPool: vi.fn(),
  };
});

function buildProvider(currentUser: FakeCognitoUser | null): CognitoAuthProvider {
  const fakePool: Pick<CognitoUserPool, 'getCurrentUser'> = {
    getCurrentUser: vi.fn(
      () => currentUser as unknown as ReturnType<CognitoUserPool['getCurrentUser']>,
    ),
  };
  return new CognitoAuthProvider(
    { userPoolId: 'ap-northeast-1_test', clientId: 'test-client-id' },
    () => fakePool as CognitoUserPool,
  );
}

describe('CognitoAuthProvider.signIn', () => {
  it('resolves to a SUCCESS result on successful SRP authentication', async () => {
    const session = makeFakeSession({
      idJwt: 'id-token-xyz',
      accessJwt: 'access-token-xyz',
      expiresAt: 1_800_000_000,
    });
    fakeUserBehavior.current = {
      authenticateUser: vi.fn((_details, callbacks: { onSuccess: (s: FakeSession) => void }) => {
        callbacks.onSuccess(session);
      }),
      getSession: vi.fn(),
      signOut: vi.fn(),
    };

    const provider = buildProvider(null);
    const result = await provider.signIn('admin@example.com', 'CorrectPassword!1');

    expect(result.kind).toBe('SUCCESS');
    if (result.kind !== 'SUCCESS') {
      throw new Error('unexpected kind');
    }
    expect(result.tokens.idToken).toBe('id-token-xyz');
    expect(result.tokens.accessToken).toBe('access-token-xyz');
    expect(result.tokens.expiresAtEpochSeconds).toBe(1_800_000_000);
    expect(fakeUserBehavior.current.authenticateUser).toHaveBeenCalledOnce();
  });

  it('rejects with AuthenticationFailedError carrying the Cognito error code', async () => {
    const cognitoErr = Object.assign(new Error('Incorrect username or password.'), {
      code: 'NotAuthorizedException',
    });
    fakeUserBehavior.current = {
      authenticateUser: vi.fn((_details, callbacks: { onFailure: (e: unknown) => void }) => {
        callbacks.onFailure(cognitoErr);
      }),
      getSession: vi.fn(),
      signOut: vi.fn(),
    };

    const provider = buildProvider(null);
    await expect(provider.signIn('a@b.com', 'wrong')).rejects.toBeInstanceOf(
      AuthenticationFailedError,
    );
    await expect(provider.signIn('a@b.com', 'wrong')).rejects.toMatchObject({
      code: 'NotAuthorizedException',
    });
  });

  it('resolves to NEW_PASSWORD_REQUIRED challenge when Cognito demands new password', async () => {
    fakeUserBehavior.current = {
      authenticateUser: vi.fn(
        (
          _details,
          callbacks: {
            newPasswordRequired: (
              userAttributes: Record<string, string>,
              requiredAttributes: string[],
            ) => void;
          },
        ) => {
          callbacks.newPasswordRequired({ email_verified: 'true' }, []);
        },
      ),
      getSession: vi.fn(),
      signOut: vi.fn(),
    };

    const provider = buildProvider(null);
    const result = await provider.signIn('a@b.com', 'temp');
    expect(result.kind).toBe('NEW_PASSWORD_REQUIRED');
    if (result.kind !== 'NEW_PASSWORD_REQUIRED') {
      throw new Error('unexpected kind');
    }
    expect(typeof result.complete).toBe('function');
  });

  it('completes new password challenge and returns refreshed TokenSet', async () => {
    const refreshedSession = makeFakeSession({
      idJwt: 'after-change-id',
      accessJwt: 'after-change-access',
      expiresAt: 1_900_000_000,
    });
    const completeNewPasswordChallenge = vi.fn(
      (
        _newPassword: string,
        _attrs: Record<string, string>,
        callbacks: { onSuccess: (s: FakeSession) => void },
      ) => {
        callbacks.onSuccess(refreshedSession);
      },
    );
    fakeUserBehavior.current = {
      authenticateUser: vi.fn(
        (
          _details,
          callbacks: {
            newPasswordRequired: (
              userAttributes: Record<string, string>,
              requiredAttributes: string[],
            ) => void;
          },
        ) => {
          callbacks.newPasswordRequired({ email_verified: 'true', name: 'A' }, []);
        },
      ),
      getSession: vi.fn(),
      signOut: vi.fn(),
      completeNewPasswordChallenge,
    } as unknown as FakeCognitoUser;

    const provider = buildProvider(null);
    const result = await provider.signIn('a@b.com', 'temp');
    if (result.kind !== 'NEW_PASSWORD_REQUIRED') {
      throw new Error('expected NEW_PASSWORD_REQUIRED');
    }

    const tokens = await result.complete('NewStrongP@ss1');
    expect(tokens.idToken).toBe('after-change-id');
    expect(tokens.expiresAtEpochSeconds).toBe(1_900_000_000);

    // issue #3 6 巡目：`completeNewPasswordChallenge` の第 2 引数は空 `{}` を渡す。
    // `userAttributes` を渡すと `sub` 等 immutable 属性を含み Cognito が
    // `NotAuthorizedException` を返す（実機検証済み）。
    expect(completeNewPasswordChallenge).toHaveBeenCalledOnce();
    const firstCall = completeNewPasswordChallenge.mock.calls[0];
    if (firstCall === undefined) {
      throw new Error('completeNewPasswordChallenge should have been called');
    }
    const passedAttrs = firstCall[1] as Record<string, string>;
    expect(passedAttrs).toEqual({});
  });

  it('rejects complete() with AuthenticationFailedError on InvalidPasswordException', async () => {
    const cognitoErr = Object.assign(new Error('Password does not meet policy.'), {
      code: 'InvalidPasswordException',
    });
    fakeUserBehavior.current = {
      authenticateUser: vi.fn(
        (
          _details,
          callbacks: {
            newPasswordRequired: (
              userAttributes: Record<string, string>,
              requiredAttributes: string[],
            ) => void;
          },
        ) => {
          callbacks.newPasswordRequired({}, []);
        },
      ),
      getSession: vi.fn(),
      signOut: vi.fn(),
      completeNewPasswordChallenge: vi.fn(
        (
          _newPassword: string,
          _attrs: Record<string, string>,
          callbacks: { onFailure: (e: unknown) => void },
        ) => {
          callbacks.onFailure(cognitoErr);
        },
      ),
    } as unknown as FakeCognitoUser;

    const provider = buildProvider(null);
    const result = await provider.signIn('a@b.com', 'temp');
    if (result.kind !== 'NEW_PASSWORD_REQUIRED') {
      throw new Error('expected NEW_PASSWORD_REQUIRED');
    }
    await expect(result.complete('weak')).rejects.toMatchObject({
      name: 'AuthenticationFailedError',
      code: 'InvalidPasswordException',
    });
  });

  it('rejects complete() when Cognito returns non-empty requiredAttributes', async () => {
    fakeUserBehavior.current = {
      authenticateUser: vi.fn(
        (
          _details,
          callbacks: {
            newPasswordRequired: (
              userAttributes: Record<string, string>,
              requiredAttributes: string[],
            ) => void;
          },
        ) => {
          callbacks.newPasswordRequired({}, ['name', 'family_name']);
        },
      ),
      getSession: vi.fn(),
      signOut: vi.fn(),
      completeNewPasswordChallenge: vi.fn(),
    } as unknown as FakeCognitoUser;

    const provider = buildProvider(null);
    const result = await provider.signIn('a@b.com', 'temp');
    if (result.kind !== 'NEW_PASSWORD_REQUIRED') {
      throw new Error('expected NEW_PASSWORD_REQUIRED');
    }
    await expect(result.complete('NewStrongP@ss1')).rejects.toMatchObject({
      name: 'AuthenticationFailedError',
      code: 'RequiredAttributesUnsupported',
    });
  });
});

describe('CognitoAuthProvider.getCurrentSession', () => {
  it('returns null when no current user is stored', async () => {
    const provider = buildProvider(null);
    await expect(provider.getCurrentSession()).resolves.toBeNull();
  });

  it('returns a TokenSet when SDK provides a valid (possibly auto-refreshed) session', async () => {
    const refreshedSession = makeFakeSession({
      idJwt: 'refreshed-id',
      accessJwt: 'refreshed-access',
      expiresAt: 9_999_999_999,
      valid: true,
    });
    const fakeUser: FakeCognitoUser = {
      authenticateUser: vi.fn(),
      getSession: vi.fn((cb: (err: Error | null, session: CognitoUserSession | null) => void) => {
        cb(null, refreshedSession as unknown as CognitoUserSession);
      }),
      signOut: vi.fn(),
    };
    const provider = buildProvider(fakeUser);

    const tokens = await provider.getCurrentSession();
    expect(tokens).not.toBeNull();
    expect(tokens?.idToken).toBe('refreshed-id');
    expect(tokens?.expiresAtEpochSeconds).toBe(9_999_999_999);
  });

  it('throws SessionExpiredError when SDK getSession yields an error (refresh failed)', async () => {
    const fakeUser: FakeCognitoUser = {
      authenticateUser: vi.fn(),
      getSession: vi.fn((cb: (err: Error | null, session: CognitoUserSession | null) => void) => {
        cb(new Error('Refresh Token has expired'), null);
      }),
      signOut: vi.fn(),
    };
    const provider = buildProvider(fakeUser);

    await expect(provider.getCurrentSession()).rejects.toBeInstanceOf(SessionExpiredError);
  });

  it('returns null when SDK reports an invalid session (no refresh succeeded)', async () => {
    const invalidSession = makeFakeSession({ valid: false });
    const fakeUser: FakeCognitoUser = {
      authenticateUser: vi.fn(),
      getSession: vi.fn((cb: (err: Error | null, session: CognitoUserSession | null) => void) => {
        cb(null, invalidSession as unknown as CognitoUserSession);
      }),
      signOut: vi.fn(),
    };
    const provider = buildProvider(fakeUser);

    await expect(provider.getCurrentSession()).resolves.toBeNull();
  });
});

describe('CognitoAuthProvider.signOut', () => {
  it('resolves immediately when there is no current user', async () => {
    const provider = buildProvider(null);
    await expect(provider.signOut()).resolves.toBeUndefined();
  });

  it('invokes the SDK signOut when a user is present', async () => {
    const fakeUser: FakeCognitoUser = {
      authenticateUser: vi.fn(),
      getSession: vi.fn(),
      signOut: vi.fn((cb?: () => void) => {
        cb?.();
      }),
    };
    const provider = buildProvider(fakeUser);
    await provider.signOut();
    expect(fakeUser.signOut).toHaveBeenCalledOnce();
  });
});
