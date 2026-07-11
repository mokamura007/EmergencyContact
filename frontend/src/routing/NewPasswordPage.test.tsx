/**
 * NewPasswordPage の単体テスト（ε-2 + issue #3 再々修正）。
 *
 * 観点：
 *   - authChallengeStore に challenge が無い場合は /login へ replace 遷移する。
 *   - authChallengeStore に challenge がある場合はフォーム描画 → complete() を呼び /
 *     成功時 / へ遷移。
 *   - 各種入力バリデーション / エラー表示。
 *
 * Validates: ε-2 NEW_PASSWORD_REQUIRED チャレンジ UI 対応 + issue #3 DataCloneError 回避
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { clearPendingChallenge, setPendingChallenge } from '../auth/authChallengeStore';
import { AuthenticationFailedError } from '../auth/errors';
import type { NewPasswordRequiredChallenge, TokenSet } from '../auth/types';

import { NewPasswordPage } from './NewPasswordPage';

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={['/new-password']}>
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

function makeChallenge(
  complete: NewPasswordRequiredChallenge['complete'],
): NewPasswordRequiredChallenge {
  return { kind: 'NEW_PASSWORD_REQUIRED', complete };
}

describe('NewPasswordPage', () => {
  beforeEach(() => {
    clearPendingChallenge();
  });
  afterEach(() => {
    clearPendingChallenge();
  });

  it('authChallengeStore に challenge が無いと /login へリダイレクトする', () => {
    renderPage();
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });

  it('authChallengeStore に challenge があるとフォームが描画される', () => {
    setPendingChallenge(makeChallenge(vi.fn()));
    renderPage();
    expect(screen.getByText('初回パスワード変更')).toBeInTheDocument();
    expect(screen.getByTestId('new-password-input')).toBeInTheDocument();
    expect(screen.getByTestId('confirm-password-input')).toBeInTheDocument();
    expect(screen.getByTestId('new-password-submit')).toBeInTheDocument();
  });

  it('新パスワード一致 + complete() 成功で / へ遷移する', async () => {
    const complete = vi.fn().mockResolvedValue(successTokens);
    setPendingChallenge(makeChallenge(complete));
    renderPage();

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'NewStrongP@ss1');
    await user.type(screen.getByTestId('confirm-password-input'), 'NewStrongP@ss1');
    await user.click(screen.getByTestId('new-password-submit'));

    expect(await screen.findByTestId('admin-home')).toBeInTheDocument();
    expect(complete).toHaveBeenCalledWith('NewStrongP@ss1');
  });

  it('確認パスワード不一致ではエラーを表示し complete() を呼ばない', async () => {
    const complete = vi.fn().mockResolvedValue(successTokens);
    setPendingChallenge(makeChallenge(complete));
    renderPage();

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
    setPendingChallenge(makeChallenge(complete));
    renderPage();

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'weak');
    await user.type(screen.getByTestId('confirm-password-input'), 'weak');
    await user.click(screen.getByTestId('new-password-submit'));

    expect(await screen.findByTestId('new-password-error')).toHaveTextContent(
      'パスワードがポリシーを満たしていません',
    );
    expect(screen.queryByTestId('admin-home')).not.toBeInTheDocument();
  });

  it('NotAuthorizedException では SRP セッション消費のメッセージ + 再ログイン動線を表示する（issue #3 4 巡目）', async () => {
    const complete = vi
      .fn()
      .mockRejectedValue(
        new AuthenticationFailedError('Incorrect username or password.', 'NotAuthorizedException'),
      );
    setPendingChallenge(makeChallenge(complete));
    renderPage();

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'NewPass2026!');
    await user.type(screen.getByTestId('confirm-password-input'), 'NewPass2026!');
    await user.click(screen.getByTestId('new-password-submit'));

    const errorEl = await screen.findByTestId('new-password-error');
    expect(errorEl).toHaveTextContent('認証セッションが無効化されました');
    expect(errorEl).toHaveTextContent('同じセッションでは再試行できない');
    expect(errorEl).toHaveTextContent('再度ログインしてください');

    // 送信ボタンは消え、再ログインボタンが出現する。
    expect(screen.queryByTestId('new-password-submit')).not.toBeInTheDocument();
    expect(screen.getByTestId('new-password-relogin')).toBeInTheDocument();

    // 入力欄も無効化されている（再試行できないことを視覚的にも保証）。
    expect(screen.getByTestId('new-password-input')).toBeDisabled();
    expect(screen.getByTestId('confirm-password-input')).toBeDisabled();
  });

  it('セッション無効化後の再ログインボタンで /login に遷移する', async () => {
    const complete = vi
      .fn()
      .mockRejectedValue(
        new AuthenticationFailedError('Incorrect username or password.', 'NotAuthorizedException'),
      );
    setPendingChallenge(makeChallenge(complete));
    renderPage();

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'NewPass2026!');
    await user.type(screen.getByTestId('confirm-password-input'), 'NewPass2026!');
    await user.click(screen.getByTestId('new-password-submit'));

    await screen.findByTestId('new-password-relogin');
    await user.click(screen.getByTestId('new-password-relogin'));

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
  });

  it('未知コードでは汎用文言 + コード併記で表示する', async () => {
    const complete = vi
      .fn()
      .mockRejectedValue(new AuthenticationFailedError('boom', 'UnknownInternalError'));
    setPendingChallenge(makeChallenge(complete));
    renderPage();

    const user = userEvent.setup();
    await user.type(screen.getByTestId('new-password-input'), 'NewStrongP@ss1');
    await user.type(screen.getByTestId('confirm-password-input'), 'NewStrongP@ss1');
    await user.click(screen.getByTestId('new-password-submit'));

    expect(await screen.findByTestId('new-password-error')).toHaveTextContent(
      'パスワード変更に失敗しました。時間をおいて再度お試しください。（コード: UnknownInternalError）',
    );
  });

  it('UI に「一時パスワードとは異なる値を設定してください」の注意書きが表示される', () => {
    setPendingChallenge(makeChallenge(vi.fn()));
    renderPage();
    // 「一時パスワードとは異なる」は複数要素にまたがる（<strong> で切れる）ため getAllByText を利用。
    expect(screen.getAllByText(/一時パスワードとは/)[0]).toBeInTheDocument();
    expect(screen.getByText(/8 文字以上/)).toBeInTheDocument();
    expect(screen.getByText(/1 回のみ有効/)).toBeInTheDocument();
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
    setPendingChallenge(makeChallenge(complete));
    renderPage();

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
    setPendingChallenge(makeChallenge(complete));
    renderPage();

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
    setPendingChallenge(makeChallenge(complete));
    renderPage();

    const user = userEvent.setup();
    await user.click(screen.getByTestId('new-password-submit'));

    expect(screen.getByTestId('new-password-error')).toHaveTextContent(
      '新パスワードと確認用パスワードの両方を入力してください',
    );
    expect(complete).not.toHaveBeenCalled();
  });
});
