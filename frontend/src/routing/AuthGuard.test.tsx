/**
 * AuthGuard コンポーネントの振る舞いテスト。
 *
 * Validates: Requirements 1.3, 1.4, 1.9, Property 1
 *
 * シナリオ：
 *   - セッション null → /login へリダイレクト（Requirement 1.4 のうち未認証経路）
 *   - セッション有り + Administrator → 子ルート描画（Requirement 1.3）
 *   - セッション有り + Administrator 非所属 → /forbidden へリダイレクト（Req 1.4 / 1.9）
 *   - セッション有り + 空 cognito:groups → /forbidden（Req 1.9：一般社員ロールは無い）
 *   - JWT 解析失敗 → /forbidden（壊れたトークンを管理機能に通さない）
 *   - SessionExpiredError → /login（再ログイン誘導）
 *   - 解決前は loading 表示
 */

import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { SessionExpiredError } from '../auth/errors';
import type { AuthSessionProvider, TokenSet } from '../auth/types';

import { AuthGuard } from './AuthGuard';

/**
 * 任意の `cognito:groups` を含む擬似 JWT を生成する。
 * 署名は本テストでは検証しないため固定値。
 */
function buildJwtWithGroups(groups: readonly string[] | string | undefined): string {
  const payload: Record<string, unknown> = { sub: 'user-1' };
  if (groups !== undefined) {
    payload['cognito:groups'] = groups;
  }
  const json = JSON.stringify(payload);
  const bytes = new TextEncoder().encode(json);
  let binary = '';
  for (const b of bytes) {
    binary += String.fromCharCode(b);
  }
  const b64u = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.${b64u}.signature`;
}

/**
 * テスト用のフェイク AuthSessionProvider。
 * `getCurrentSession` の挙動は呼出側でカスタマイズする。
 */
function makeFakeProvider(behaviour: () => Promise<TokenSet | null>): AuthSessionProvider {
  return {
    signIn: () => Promise.reject(new Error('signIn should not be called in AuthGuard tests')),
    getCurrentSession: behaviour,
    signOut: () => Promise.resolve(),
  };
}

function renderWithRoutes(provider: AuthSessionProvider, initialPath = '/'): void {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<AuthGuard authProvider={provider} />}>
          <Route path="/" element={<div data-testid="admin-content">admin-content</div>} />
        </Route>
        <Route path="/login" element={<div data-testid="login-page">login-page</div>} />
        <Route path="/forbidden" element={<div data-testid="forbidden-page">forbidden-page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AuthGuard', () => {
  it('セッション解決前は role="status" のローディング表示を出す', () => {
    const provider = makeFakeProvider(
      () =>
        new Promise<TokenSet | null>(() => {
          /* never resolves */
        }),
    );
    renderWithRoutes(provider);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('Requirement 1.3: Administrator グループ所属の場合は子ルートを描画する', async () => {
    const provider = makeFakeProvider(() =>
      Promise.resolve<TokenSet>({
        idToken: buildJwtWithGroups(['Administrator']),
        accessToken: 'access',
        expiresAtEpochSeconds: 9_999_999_999,
      }),
    );
    renderWithRoutes(provider);
    expect(await screen.findByTestId('admin-content')).toBeInTheDocument();
  });

  it('Requirement 1.4: 未認証セッション（null）は /login へリダイレクトする', async () => {
    const provider = makeFakeProvider(() => Promise.resolve(null));
    renderWithRoutes(provider);
    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
  });

  it('Requirement 1.4: SessionExpiredError も /login へリダイレクトする', async () => {
    const provider = makeFakeProvider(() => Promise.reject(new SessionExpiredError()));
    renderWithRoutes(provider);
    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
  });

  it('Requirement 1.4 / 1.9: Administrator 非所属（他グループのみ）の場合は /forbidden へ', async () => {
    const provider = makeFakeProvider(() =>
      Promise.resolve<TokenSet>({
        idToken: buildJwtWithGroups(['Auditor', 'Reviewer']),
        accessToken: 'access',
        expiresAtEpochSeconds: 9_999_999_999,
      }),
    );
    renderWithRoutes(provider);
    expect(await screen.findByTestId('forbidden-page')).toBeInTheDocument();
  });

  it('Requirement 1.9: cognito:groups クレーム自体が無いトークンは /forbidden へ', async () => {
    const provider = makeFakeProvider(() =>
      Promise.resolve<TokenSet>({
        idToken: buildJwtWithGroups(undefined),
        accessToken: 'access',
        expiresAtEpochSeconds: 9_999_999_999,
      }),
    );
    renderWithRoutes(provider);
    expect(await screen.findByTestId('forbidden-page')).toBeInTheDocument();
  });

  it('Property 1（境界）: 大文字違い "ADMINISTRATOR" は管理者と認めず /forbidden へ', async () => {
    const provider = makeFakeProvider(() =>
      Promise.resolve<TokenSet>({
        idToken: buildJwtWithGroups(['ADMINISTRATOR']),
        accessToken: 'access',
        expiresAtEpochSeconds: 9_999_999_999,
      }),
    );
    renderWithRoutes(provider);
    expect(await screen.findByTestId('forbidden-page')).toBeInTheDocument();
  });

  it('JWT 形式不正のトークンを保持していても /forbidden に倒す（管理機能は出さない）', async () => {
    // セグメント数 1 の不正トークン。
    const provider = makeFakeProvider(() =>
      Promise.resolve<TokenSet>({
        idToken: 'not-a-jwt',
        accessToken: 'access',
        expiresAtEpochSeconds: 9_999_999_999,
      }),
    );
    renderWithRoutes(provider);
    expect(await screen.findByTestId('forbidden-page')).toBeInTheDocument();
  });

  it('予期しないエラー（ネットワーク例外等）は /login に倒す', async () => {
    const provider = makeFakeProvider(() => Promise.reject(new Error('network down')));
    renderWithRoutes(provider);
    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
  });

  it('Administrator が他グループと併存する場合も管理機能を表示する', async () => {
    const provider = makeFakeProvider(() =>
      Promise.resolve<TokenSet>({
        idToken: buildJwtWithGroups(['Auditor', 'Administrator', 'Reviewer']),
        accessToken: 'access',
        expiresAtEpochSeconds: 9_999_999_999,
      }),
    );
    renderWithRoutes(provider);
    expect(await screen.findByTestId('admin-content')).toBeInTheDocument();
    // 念のためローディング状態が解除されたことを確認。
    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
    });
  });
});
