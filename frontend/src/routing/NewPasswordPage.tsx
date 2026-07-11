/**
 * 初回パスワード変更ページ（ε-2 修正、issue #3 再修正シリーズ）。
 *
 * 振る舞い：
 *   - LoginPage で `signIn` が `NEW_PASSWORD_REQUIRED` を返した場合、
 *     `setPendingChallenge(challenge)` の後 `navigate('/new-password')` で
 *     本ページに遷移する。DataCloneError 回避のため history.state は使わない。
 *     マウント時に `consumePendingChallenge` で取り出せない場合は不正アクセス
 *     （直接 URL 入力 / リロード等）とみなし `/login` に replace 遷移する。
 *   - 新パスワード + 確認パスワードを入力 → 一致確認 →
 *     `challenge.complete(newPassword)` を呼ぶ。
 *   - 成功 → ダッシュボード `/` へ replace 遷移。
 *   - 失敗（パスワードポリシー違反等）→ `AuthenticationFailedError.code`
 *     に応じてメッセージを開示し、再入力可能な状態に戻す。
 *   - `NotAuthorizedException` の場合、SRP セッションは既に消費済で
 *     同じ challenge の再送信は不可能。UI を「再ログインへ」動線に切替える
 *     （フォーム非活性化 + 再ログインボタン表示、issue #3 4 巡目対応）。
 *
 * 設計判断：
 *   - 19 原則 (b)：失敗時のフォールバックを禁止し、サーバーから返った
 *     エラーコードを画面に開示する。
 *   - URL は `/new-password` 固定。リロードで challenge が失われた場合は
 *     再ログイン誘導とする。
 *   - パスワード強度のクライアント側チェックは行わず Cognito 側の
 *     パスワードポリシーに委ねる（DRY、二重実装回避）。
 */

import { useCallback, useMemo, useState, type FormEvent, type JSX } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';

import { clearPendingChallenge, consumePendingChallenge } from '../auth/authChallengeStore';
import { AuthenticationFailedError } from '../auth/errors';
import type { NewPasswordRequiredChallenge } from '../auth/types';

function translateCompleteError(err: unknown): string {
  if (err instanceof AuthenticationFailedError) {
    if (err.code === 'InvalidPasswordException') {
      return 'パスワードがポリシーを満たしていません。8 文字以上、英大文字・小文字・数字・記号を含めてください。';
    }
    if (err.code === 'InvalidParameterException') {
      return 'パスワード形式が不正です。入力内容を確認してください。';
    }
    if (err.code === 'NotAuthorizedException') {
      // issue #3 4 巡目訂正：Cognito の SRP challenge Session は 1 回限り。
      // 一度失敗するとその challenge オブジェクトは再利用不能。
      // 過去に「新パスワード=一時パスワード」の可能性を挙げたが、SDK 内部
      // Session 消費が最有力仮説である（実機で異なる値でも `NotAuthorizedException`
      // を確認）。ユーザーには再ログイン動線を強制する。
      return (
        '認証セッションが無効化されました。' +
        '一度パスワード設定に失敗すると同じセッションでは再試行できないため、' +
        '再度ログインしてください。次回は一時パスワードとは異なる新しいパスワードで一度で完了させてください。'
      );
    }
    if (err.code === 'RequiredAttributesUnsupported') {
      return '初回ログインで追加属性の入力が必要です。システム管理者にお問い合わせください。';
    }
    // issue #3 追加修正：未知コードでも識別子を併記し切り分けを容易化。
    return `パスワード変更に失敗しました。時間をおいて再度お試しください。（コード: ${err.code}）`;
  }
  return 'パスワード変更に失敗しました。時間をおいて再度お試しください。';
}

/**
 * `err` が「SRP チャレンジセッション無効化」を示すかを判定する。
 * true の場合、同じ challenge での再送信は必ず失敗するため、UI 側で
 * 再ログイン動線に切替える必要がある。
 */
function isSessionInvalidError(err: unknown): boolean {
  return err instanceof AuthenticationFailedError && err.code === 'NotAuthorizedException';
}

export function NewPasswordPage(): JSX.Element {
  const navigate = useNavigate();
  // issue #3 再々修正：DataCloneError 回避のため history.state を使わず、
  // authChallengeStore（モジュールスコープの一時ストア）から取り出す。
  const challenge = useMemo<NewPasswordRequiredChallenge | null>(
    () => consumePendingChallenge(),
    [],
  );

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // issue #3 4 巡目：セッション無効化を検知したら true にし、UI を
  // 「再ログイン導線のみ」に切替える。フォーム再送信は不可能。
  const [sessionInvalid, setSessionInvalid] = useState(false);

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (challenge === null || sessionInvalid) {
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
          if (isSessionInvalidError(err)) {
            // 同一 challenge の再送信は不可能なので、pending ストアも念のため消し、
            // UI を再ログイン動線モードに切替える。
            clearPendingChallenge();
            setSessionInvalid(true);
          }
        } finally {
          setSubmitting(false);
        }
      })();
    },
    [challenge, sessionInvalid, newPassword, confirmPassword, navigate],
  );

  const handleReLogin = useCallback(() => {
    clearPendingChallenge();
    navigate('/login', { replace: true });
  }, [navigate]);

  if (challenge === null) {
    // 不正アクセス（直接 URL 入力 / リロードで store 喪失）→ ログインへ。
    return <Navigate to="/login" replace />;
  }

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: '2rem', maxWidth: '420px' }}>
      <h1>初回パスワード変更</h1>
      <p>
        初回ログインのため、新しいパスワードを設定してください。
        設定完了後、ダッシュボードに進みます。
      </p>
      <p style={{ color: '#b45309', fontSize: '0.9rem', marginTop: '-0.5rem' }}>
        ※ 一時パスワードとは<strong>異なる</strong>値を設定してください。 パスワードポリシー：8
        文字以上、英大文字・小文字・数字・記号を含む。
        <br />※ 認証セッションは 1 回のみ有効です。一度失敗すると再ログインが必要になります。
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
            disabled={sessionInvalid}
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
            disabled={sessionInvalid}
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
        {sessionInvalid ? (
          <button
            type="button"
            onClick={handleReLogin}
            style={{ padding: '0.5rem 1rem' }}
            data-testid="new-password-relogin"
          >
            ログイン画面に戻る
          </button>
        ) : (
          <button
            type="submit"
            disabled={submitting}
            style={{ padding: '0.5rem 1rem' }}
            data-testid="new-password-submit"
          >
            {submitting ? '設定中…' : 'パスワードを設定'}
          </button>
        )}
      </form>
    </main>
  );
}
