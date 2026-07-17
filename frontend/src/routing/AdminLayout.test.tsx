/**
 * AdminLayout の単体テスト（ε-1 ナビ一貫性、15.2a 実機検証 後追い修正）。
 *
 * 観点：
 *   - ヘッダにシステム名 / ダッシュボードへ戻るリンク / ログアウトボタンが描画される。
 *   - ダッシュボードへ戻るリンクが <a href="/"> として描画される（react-router の Link）。
 *   - 任意の子ルート pathname でもヘッダのナビが消えない（共通レイアウトとして全画面に出る）。
 *   - ログアウトボタン押下で `signOut` が呼ばれ `/login` に replace 遷移する。
 *
 * Validates: ε-1 ナビゲーション一貫性（DRY）
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { AuthSessionProvider, SignInResult, TokenSet } from '../auth/types';

import { AdminHome, AdminLayout } from './AdminLayout';

function makeProvider(overrides: Partial<AuthSessionProvider> = {}): AuthSessionProvider {
  return {
    signIn: (): Promise<SignInResult> =>
      Promise.resolve({
        kind: 'SUCCESS',
        tokens: {
          idToken: 'id',
          accessToken: 'access',
          expiresAtEpochSeconds: 9_999_999_999,
        } as TokenSet,
      }),
    getCurrentSession: () => Promise.resolve(null),
    signOut: () => Promise.resolve(),
    ...overrides,
  };
}

function renderLayout(
  initialEntries: string[],
  provider: AuthSessionProvider,
): {
  child: HTMLElement | null;
} {
  render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route element={<AdminLayout authProvider={provider} />}>
          <Route index element={<AdminHome />} />
          <Route path="employees" element={<div data-testid="child-employees">employees</div>} />
          <Route path="dictionary" element={<div data-testid="child-dictionary">dict</div>} />
        </Route>
        <Route path="/login" element={<div data-testid="login-page">login</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return { child: screen.queryByTestId('child-employees') };
}

describe('AdminLayout', () => {
  it('ヘッダにシステム名 / ダッシュボードへ戻るリンク / ログアウトボタンを描画する', () => {
    renderLayout(['/'], makeProvider());

    expect(screen.getByText('安否確認システム 管理サイト')).toBeInTheDocument();
    expect(screen.getByTestId('admin-layout-home-link')).toBeInTheDocument();
    expect(screen.getByTestId('admin-layout-signout')).toBeInTheDocument();
  });

  it('ダッシュボードへ戻るリンクが <a href="/"> として描画される', () => {
    renderLayout(['/employees'], makeProvider());

    const homeLink = screen.getByTestId('admin-layout-home-link');
    // <Link to="/"> は react-router-dom により <a href="/"> として描画される。
    expect(homeLink.tagName.toLowerCase()).toBe('a');
    expect(homeLink).toHaveAttribute('href', '/');
  });

  it('子ルート（/employees, /dictionary）でも共通ナビが消えない', () => {
    renderLayout(['/employees'], makeProvider());
    expect(screen.getByTestId('admin-layout-home-link')).toBeInTheDocument();
    expect(screen.getByTestId('child-employees')).toBeInTheDocument();
  });

  it('ログアウトボタン押下で signOut → /login へ遷移する', async () => {
    const signOut = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderLayout(['/employees'], makeProvider({ signOut }));

    await user.click(screen.getByTestId('admin-layout-signout'));

    expect(signOut).toHaveBeenCalledOnce();
    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
  });

  it('AdminHome がメニュー一覧（社員 / サイクル / インバウンド / 辞書）リンクを提供する', () => {
    renderLayout(['/'], makeProvider());

    // AdminHome のメニューリンク存在を検証
    expect(screen.getByRole('link', { name: '社員マスタ管理' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '安否確認 起動' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '安否確認 履歴' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '着信履歴' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'キーワード辞書管理' })).toBeInTheDocument();
  });
});
