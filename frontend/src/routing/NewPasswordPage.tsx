/**
 * 初回パスワード変更ページ（ε-2 修正、15.2a 実機検証）。
 *
 * 振る舞い：
 *   - LoginPage で `signIn` が `NEW_PASSWORD_REQUIRED` を返した場合、
 *     `navigate('/new-password', { state: { challenge } })` で本ページに
 *     遷移する。history.state に challenge オブジェクト（complete 関数）
 *     が含まれない場合は、不正アクセス（直接 URL 入力 / リロード等）と
 *     みなし `/login` に replace 遷移する。
 *   - 新パスワード + 確認パスワードを入力 → 一致確認 →
 *     `challenge.complete(newPassword)` を呼ぶ。
 *   - 成功 → ダッシュボード `/` へ replace 遷移。Cognito が返した TokenSet
 *     は SDK 内部で localStorage に保存されるため、AuthGuard が次回マウント
 *     時に有効セッションを検出してそのまま管理画面へ進める。
 *   - 失敗（パスワードポリシー違反等）→ `AuthenticationFailedError.code`
 *     に応じてメッセージを開示し、フォームを再入力可能に戻す。
 *
 * 設計判断：
 *   - 19 原則 (b)：失敗時のフォールバックを禁止し、サーバーから返った
 *     エラーコードを画面に開示する。
 *   - URL は `/new-password` 固定。リロードで challenge が失われた場合は
 *     再ログイン誘導とする（仕様として受容可能、Q3 A 案で確認済み）。
 *   - パスワード強度のクライアント側チェックは行わず Cognito 側の
 *     パスワードポリシーに委ねる（DRY、二重実装回避）。
 */

import { useCallback, useState, type FormEvent, type JSX } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { AuthenticationFailedError } from '../auth/errors';
import type { NewPasswordRequiredChallenge } from '../auth/types';

interface LocationStateShape {
  readonly challenge?: NewPasswordRequiredChallenge;
}

function extractChallenge(state: unknown): NewPasswordRequiredChallenge | null {
  if (state !== null && typeof state === 'object') {
    const candidate = (state as LocationStateShape).challenge;
    if (
      candidate !== undefined &&
      candidate !== null &&
      typeof candidate === 'object' &&
      candidate.kind === 'NEW_PASSWORD_REQUIRED' &&
      typeof candidate.complete === 'function'
    ) {
      return candidate;
    }
  }
  return null;
}

function translateCompleteError(err: unknown): string {
  if (err instanceof AuthenticationFailedError) {
    if (err.code === 'InvalidPasswordException') {
      return 'パスワードがポリシーを満たしていません。8 文字以上、英大文字・小文字・数字・記号を含めてください。';
    }
    if (err.code === 'InvalidParameterException') {
      return 'パスワード形式が不正です。入力内容を確認してください。';
    }
    if (err.code === 'NotAuthorizedException') {
      return 'セッションが無効になりました。再度ログインしてください。';
    }
    if (err.code === 'RequiredAttributesUnsupported') {
      return '初回ログインで追加属性の入力が必要です。システム管理者にお問い合わせください。';
    }
    return 'パスワード変更に失敗しました。時間をおいて再度お試しください。';
  }
  return 'パスワード変更に失敗しました。時間をおいて再度お試しください。';
}

export function NewPasswordPage(): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const challenge = extractChallenge(location.state);

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (challenge === null) {
        return;
      }
      if (newPassword === '' || confirmPassword === '') {
        setErrorMessage('新パスワードと確認用パスワードの両方を入力してください。');
        return;
      }
      if (newPassword !== confirmPassword) {
        setErrorMessage('確認用パスワードが一致しません。');
        return;
      }
      setSubmitting(true);
      setErrorMessage(null);

      void (async () => {
        try {
          await challenge.complete(newPassword);
          navigate('/', { replace: true });
        } catch (err) {
          setErrorMessage(translateCompleteError(err));
        } finally {
          setSubmitting(false);
        }
      })();
    },
    [challenge, newPassword, confirmPassword, navigate],
  );

  if (challenge === null) {
    // 不正アクセス（直接 URL 入力 / リロードで state 喪失）→ ログインへ。
    return <Navigate to="/login" replace />;
  }

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: '2rem', maxWidth: '420px' }}>
      <h1>初回パスワード変更</h1>
      <p>
        初回ログインのため、新しいパスワードを設定してください。
        設定完了後、ダッシュボードに進みます。
      </p>
      <form onSubmit={onSubmit} noValidate>
        <div style={{ marginBottom: '1rem' }}>
          <label htmlFor="new-password" style={{ display: 'block', marginBottom: '0.25rem' }}>
            新しいパスワード
          </label>
          <input
            id="new-password"
            name="newPassword"
            type="password"
            autoComplete="new-password"
            required
            value={newPassword}
            onChange={(e) => {
              setNewPassword(e.target.value);
            }}
            style={{ width: '100%', padding: '0.5rem' }}
            data-testid="new-password-input"
          />
        </div>
        <div style={{ marginBottom: '1rem' }}>
          <label htmlFor="confirm-password" style={{ display: 'block', marginBottom: '0.25rem' }}>
            新しいパスワード（確認用）
          </label>
          <input
            id="confirm-password"
            name="confirmPassword"
            type="password"
            autoComplete="new-password"
            required
            value={confirmPassword}
            onChange={(e) => {
              setConfirmPassword(e.target.value);
            }}
            style={{ width: '100%', padding: '0.5rem' }}
            data-testid="confirm-password-input"
          />
        </div>
        {errorMessage !== null && (
          <p
            role="alert"
            style={{ color: '#b91c1c', marginBottom: '1rem' }}
            data-testid="new-password-error"
          >
            {errorMessage}
          </p>
        )}
        <button
          type="submit"
          disabled={submitting}
          style={{ padding: '0.5rem 1rem' }}
          data-testid="new-password-submit"
        >
          {submitting ? '設定中…' : 'パスワードを設定'}
        </button>
      </form>
    </main>
  );
}
