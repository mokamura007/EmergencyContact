/**
 * NewPasswordPage の単体テスト（ε-2、15.2a 実機検証）。
 *
 * 観点：
 *   - location.state.challenge が無い場合は /login へ replace 遷移する。
 *   - 新パスワードと確認パスワードを入力 → 一致 → complete() 呼び出し →
 *     成功 → / へ遷移する。
 *   - 確認パスワード不一致でエラー表示、complete() は呼ばれない。
 *   - InvalidPasswordException ではポリシー説明メッセージを表示する。
 *   - 送信中はボタンが disabled。
 *
 * Validates: ε-2 NEW_PASSWORD_REQUIRED チャレンジ UI 対応
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { AuthenticationFailedError } from '../auth/errors';
import type { NewPasswordRequiredChallenge, TokenSet } from '../auth/types';

import { NewPasswordPage } from './NewPasswordPage';

function renderWithState(state: unknown): void {
  render(
    <MemoryRouter initialEntries={[{ pathname: '/new-password', state }]}>
      <Routes>
        <Route path="/new-password" element={<NewPasswordPage />} />
        <Route path="/login" element={<div data-testid="login-page">login</div>} />
        <Route path="/" element={<div data-testid="admin-home">admin-home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const successTokens: TokenSet = {
  idToken: 'id',
  accessToken: 'access',
  expiresAtEpochSeconds: 9_999_999_999,
};

describe('NewPasswordPage', () => {
  it('challenge state が無いと /login へリダイレクトする', () => {
    renderWithState(undefined);
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });

  it('challenge state があるとフォームが描画される', () => {
    const challenge: NewPasswordRequiredChallenge = {
      kind: 'NEW_PASSWORD_REQUIRED',
      complete: vi.fn(),
    };
    renderWithState({ challenge });
    expect(screen.getByText('初回パスワード変更')).toBeInTheDocument();
    expect(screen.getByTestId('new-password-input')).toBeInTheDocument();
    expect(screen.getByTestId('confirm-password-input')).toBeInTheDocument();
    expect(screen.getByTestId('new-password-submit')).toBeInTheDocument();
  });

  it('新パスワード一致 + complete() 成功で / へ遷移する', async () => {
    const complete = vi.fn().mockResolvedValue(successTokens);
    const challenge: NewPasswordRequiredChallenge = {
      kind: 'NEW_PASSWORD_REQUIRED',
      complete,
    };
    renderWithState({ challenge });

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'NewStrongP@ss1');
    await user.type(screen.getByTestId('confirm-password-input'), 'NewStrongP@ss1');
    await user.click(screen.getByTestId('new-password-submit'));

    expect(await screen.findByTestId('admin-home')).toBeInTheDocument();
    expect(complete).toHaveBeenCalledWith('NewStrongP@ss1');
  });

  it('確認パスワード不一致ではエラーを表示し complete() を呼ばない', async () => {
    const complete = vi.fn().mockResolvedValue(successTokens);
    const challenge: NewPasswordRequiredChallenge = {
      kind: 'NEW_PASSWORD_REQUIRED',
      complete,
    };
    renderWithState({ challenge });

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'NewStrongP@ss1');
    await user.type(screen.getByTestId('confirm-password-input'), 'Different!1A');
    await user.click(screen.getByTestId('new-password-submit'));

    expect(screen.getByTestId('new-password-error')).toHaveTextContent('一致しません');
    expect(complete).not.toHaveBeenCalled();
    expect(screen.queryByTestId('admin-home')).not.toBeInTheDocument();
  });

  it('InvalidPasswordException ではポリシー説明メッセージを表示する', async () => {
    const complete = vi
      .fn()
      .mockRejectedValue(
        new AuthenticationFailedError('Password does not meet policy.', 'InvalidPasswordException'),
      );
    const challenge: NewPasswordRequiredChallenge = {
      kind: 'NEW_PASSWORD_REQUIRED',
      complete,
    };
    renderWithState({ challenge });

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'weak');
    await user.type(screen.getByTestId('confirm-password-input'), 'weak');
    await user.click(screen.getByTestId('new-password-submit'));

    expect(await screen.findByTestId('new-password-error')).toHaveTextContent(
      'パスワードがポリシーを満たしていません',
    );
    expect(screen.queryByTestId('admin-home')).not.toBeInTheDocument();
  });

  it('RequiredAttributesUnsupported では管理者問合せメッセージを表示する', async () => {
    const complete = vi
      .fn()
      .mockRejectedValue(
        new AuthenticationFailedError(
          'Required attributes not supported',
          'RequiredAttributesUnsupported',
        ),
      );
    const challenge: NewPasswordRequiredChallenge = {
      kind: 'NEW_PASSWORD_REQUIRED',
      complete,
    };
    renderWithState({ challenge });

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'NewStrongP@ss1');
    await user.type(screen.getByTestId('confirm-password-input'), 'NewStrongP@ss1');
    await user.click(screen.getByTestId('new-password-submit'));

    expect(await screen.findByTestId('new-password-error')).toHaveTextContent(
      '管理者にお問い合わせください',
    );
  });

  it('送信中はボタンが disabled になりラベルが変わる', async () => {
    type Resolver = (tokens: TokenSet) => void;
    const deferred: { resolve: Resolver | null } = { resolve: null };
    const complete = vi.fn().mockImplementation(
      () =>
        new Promise<TokenSet>((resolve) => {
          deferred.resolve = resolve;
        }),
    );
    const challenge: NewPasswordRequiredChallenge = {
      kind: 'NEW_PASSWORD_REQUIRED',
      complete,
    };
    renderWithState({ challenge });

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'NewStrongP@ss1');
    await user.type(screen.getByTestId('confirm-password-input'), 'NewStrongP@ss1');
    await user.click(screen.getByTestId('new-password-submit'));

    const submitting = await screen.findByRole('button', { name: '設定中…' });
    expect(submitting).toBeDisabled();

    deferred.resolve?.(successTokens);
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '設定中…' })).not.toBeInTheDocument();
    });
  });

  it('空入力ではエラーを表示し complete() を呼ばない', async () => {
    const complete = vi.fn().mockResolvedValue(successTokens);
    const challenge: NewPasswordRequiredChallenge = {
      kind: 'NEW_PASSWORD_REQUIRED',
      complete,
    };
    renderWithState({ challenge });

    const user = userEvent.setup();
    await user.click(screen.getByTestId('new-password-submit'));

    expect(screen.getByTestId('new-password-error')).toHaveTextContent(
      '新パスワードと確認用パスワードの両方を入力してください',
    );
    expect(complete).not.toHaveBeenCalled();
  });
});
