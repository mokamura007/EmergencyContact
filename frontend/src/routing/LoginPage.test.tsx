/**
 * LoginPage コンポーネントのテスト。
 *
 * Validates: Requirements 1.1, 1.2, 1.9（管理者専用ログイン経路）+
 *            ε-2 NEW_PASSWORD_REQUIRED チャレンジ対応（15.2a 実機検証）
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { AuthenticationFailedError } from '../auth/errors';
import type {
  AuthSessionProvider,
  NewPasswordRequiredChallenge,
  SignInResult,
  TokenSet,
} from '../auth/types';

import { LoginPage } from './LoginPage';

function makeProvider(
  signIn: (email: string, password: string) => Promise<SignInResult>,
): AuthSessionProvider {
  return {
    signIn,
    getCurrentSession: () => Promise.resolve(null),
    signOut: () => Promise.resolve(),
  };
}

function renderLogin(provider: AuthSessionProvider, initialState?: unknown): void {
  render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state: initialState }]}>
      <Routes>
        <Route path="/login" element={<LoginPage authProvider={provider} />} />
        <Route path="/" element={<div data-testid="admin-home">admin-home</div>} />
        <Route path="/employees" element={<div data-testid="employees-page">employees-page</div>} />
        <Route
          path="/new-password"
          element={<div data-testid="new-password-page">new-password-page</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

const successTokens: TokenSet = {
  idToken: 'id-token',
  accessToken: 'access-token',
  expiresAtEpochSeconds: 9_999_999_999,
};
const successResult: SignInResult = { kind: 'SUCCESS', tokens: successTokens };

describe('LoginPage', () => {
  it('signIn 成功時はトップ（/）へ replace 遷移する', async () => {
    const signIn = vi.fn().mockResolvedValue(successResult);
    renderLogin(makeProvider(signIn));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('メールアドレス'), 'admin@example.com');
    await user.type(screen.getByLabelText('パスワード'), 'Passw0rd!Strong');
    await user.click(screen.getByRole('button', { name: 'ログイン' }));

    expect(await screen.findByTestId('admin-home')).toBeInTheDocument();
    expect(signIn).toHaveBeenCalledWith('admin@example.com', 'Passw0rd!Strong');
  });

  it('AuthGuard が記録した from パスへ復帰する', async () => {
    const signIn = vi.fn().mockResolvedValue(successResult);
    renderLogin(makeProvider(signIn), { from: '/employees' });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('メールアドレス'), 'admin@example.com');
    await user.type(screen.getByLabelText('パスワード'), 'Passw0rd!Strong');
    await user.click(screen.getByRole('button', { name: 'ログイン' }));

    expect(await screen.findByTestId('employees-page')).toBeInTheDocument();
  });

  it('NotAuthorizedException ではユーザー向けの非特定メッセージを表示する', async () => {
    const signIn = vi
      .fn()
      .mockRejectedValue(
        new AuthenticationFailedError('Incorrect username or password.', 'NotAuthorizedException'),
      );
    renderLogin(makeProvider(signIn));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('メールアドレス'), 'admin@example.com');
    await user.type(screen.getByLabelText('パスワード'), 'wrong');
    await user.click(screen.getByRole('button', { name: 'ログイン' }));

    expect(
      await screen.findByText('メールアドレスまたはパスワードが正しくありません。'),
    ).toBeInTheDocument();
    // 失敗時は遷移しない。
    expect(screen.queryByTestId('admin-home')).not.toBeInTheDocument();
  });

  it('NEW_PASSWORD_REQUIRED 結果では /new-password へ遷移する（ε-2）', async () => {
    const challenge: NewPasswordRequiredChallenge = {
      kind: 'NEW_PASSWORD_REQUIRED',
      complete: vi.fn().mockResolvedValue(successTokens),
    };
    const signIn = vi.fn().mockResolvedValue(challenge);
    renderLogin(makeProvider(signIn));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('メールアドレス'), 'placeholder@example.com');
    await user.type(screen.getByLabelText('パスワード'), 'TempPass1!');
    await user.click(screen.getByRole('button', { name: 'ログイン' }));

    expect(await screen.findByTestId('new-password-page')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-home')).not.toBeInTheDocument();
  });

  it('未知の AuthenticationFailedError は汎用メッセージで安全側に倒す', async () => {
    const signIn = vi
      .fn()
      .mockRejectedValue(new AuthenticationFailedError('boom', 'UnknownInternalError'));
    renderLogin(makeProvider(signIn));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('メールアドレス'), 'admin@example.com');
    await user.type(screen.getByLabelText('パスワード'), 'Passw0rd!Strong');
    await user.click(screen.getByRole('button', { name: 'ログイン' }));

    expect(
      await screen.findByText('ログインに失敗しました。時間をおいて再度お試しください。'),
    ).toBeInTheDocument();
  });

  it('送信中はボタンが disabled になりラベルが変わる', async () => {
    type Resolver = (result: SignInResult) => void;
    const deferred: { resolve: Resolver | null } = { resolve: null };
    const signIn = vi.fn().mockImplementation(
      () =>
        new Promise<SignInResult>((resolve) => {
          deferred.resolve = resolve;
        }),
    );
    renderLogin(makeProvider(signIn));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('メールアドレス'), 'admin@example.com');
    await user.type(screen.getByLabelText('パスワード'), 'Passw0rd!Strong');
    await user.click(screen.getByRole('button', { name: 'ログイン' }));

    const submitting = await screen.findByRole('button', { name: 'ログイン中…' });
    expect(submitting).toBeDisabled();

    // 後始末：未解決のままだと vitest が hang する可能性があるため解決する。
    expect(deferred.resolve).not.toBeNull();
    deferred.resolve?.(successResult);
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'ログイン中…' })).not.toBeInTheDocument();
    });
  });
});
