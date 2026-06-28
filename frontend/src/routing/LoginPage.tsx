/**
 * 管理者ログインページ。
 *
 * 対応要件：
 *   - Requirement 1.1 : TLS 上で Cognito 認証フローを要求する（SPA→Cognito）。
 *   - Requirement 1.2 : SRP 認証で ID / Access / Refresh トークンを取得する。
 *   - Requirement 1.9 : 管理者専用ログイン（一般社員ロールは提供しない）。
 *
 * 振る舞い：
 *   - フォーム送信時に `AuthSessionProvider.signIn(email, password)` を呼ぶ。
 *   - 結果が `SUCCESS` → `location.state.from`（AuthGuard が記録した遷移元）
 *     または `/` へ遷移。
 *   - 結果が `NEW_PASSWORD_REQUIRED` → `/new-password` へ navigate し、
 *     state に challenge オブジェクト（complete 関数）を渡す。
 *     UI 詰まり解消のため例外ではなく型で進行を継続させる（ε-2 修正、
 *     15.2a 実機検証）。
 *   - 失敗 → エラーメッセージを表示し、フォームを再入力可能な状態に戻す。
 *
 * 設計判断：
 *   - エラー分類は `AuthenticationFailedError.code`（Cognito の `__type`）
 *     を利用し、固定文言の i18n は将来 Phase で行う（今は日本語直書き）。
 *   - 19原則(b)：catch でフォールバック値を返さず、ユーザーに見える形で
 *     エラーを露出する（再ログイン誘導 / 運用問合せの動線）。
 */

import { useCallback, useState, type FormEvent, type JSX } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { getAuthProvider } from '../auth';
import { AuthenticationFailedError } from '../auth/errors';
import type { AuthSessionProvider } from '../auth/types';

export interface LoginPageProps {
  /** テスト用 DI。本番では `getAuthProvider()` のシングルトンを使う。 */
  readonly authProvider?: AuthSessionProvider;
}

interface LocationStateShape {
  readonly from?: string;
}

function extractFromPath(state: unknown): string {
  if (state !== null && typeof state === 'object') {
    const candidate = (state as LocationStateShape).from;
    if (typeof candidate === 'string' && candidate.startsWith('/')) {
      return candidate;
    }
  }
  return '/';
}

export function LoginPage({ authProvider }: LoginPageProps = {}): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const provider = authProvider ?? getAuthProvider();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setSubmitting(true);
      setErrorMessage(null);

      void (async () => {
        try {
          const result = await provider.signIn(email, password);
          if (result.kind === 'NEW_PASSWORD_REQUIRED') {
            // FORCE_CHANGE_PASSWORD ユーザー：新パスワード設定画面へ遷移。
            // challenge オブジェクトは history.state に保持し、新ページが
            // `complete(newPassword)` を呼んで認証を続行する。
            navigate('/new-password', { state: { challenge: result } });
            return;
          }
          const target = extractFromPath(location.state);
          navigate(target, { replace: true });
        } catch (err) {
          if (err instanceof AuthenticationFailedError) {
            // Cognito の代表的なエラーコードのみメッセージを分岐する。
            // それ以外は汎用メッセージで安全側に倒す（攻撃者に過度の情報を与えない）。
            if (err.code === 'NotAuthorizedException' || err.code === 'UserNotFoundException') {
              setErrorMessage('メールアドレスまたはパスワードが正しくありません。');
            } else if (err.code === 'PasswordResetRequiredException') {
              setErrorMessage(
                'パスワードのリセットが必要です。システム管理者にお問い合わせください。',
              );
            } else {
              setErrorMessage('ログインに失敗しました。時間をおいて再度お試しください。');
            }
          } else {
            setErrorMessage('ログインに失敗しました。時間をおいて再度お試しください。');
          }
        } finally {
          setSubmitting(false);
        }
      })();
    },
    [provider, email, password, location.state, navigate],
  );

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: '2rem', maxWidth: '420px' }}>
      <h1>管理者ログイン</h1>
      <p>安否確認システム 管理サイトへのログインには管理者アカウントが必要です。</p>
      <form onSubmit={onSubmit} noValidate>
        <div style={{ marginBottom: '1rem' }}>
          <label htmlFor="login-email" style={{ display: 'block', marginBottom: '0.25rem' }}>
            メールアドレス
          </label>
          <input
            id="login-email"
            name="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
            }}
            style={{ width: '100%', padding: '0.5rem' }}
          />
        </div>
        <div style={{ marginBottom: '1rem' }}>
          <label htmlFor="login-password" style={{ display: 'block', marginBottom: '0.25rem' }}>
            パスワード
          </label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
            }}
            style={{ width: '100%', padding: '0.5rem' }}
          />
        </div>
        {errorMessage !== null && (
          <p role="alert" style={{ color: '#b91c1c', marginBottom: '1rem' }}>
            {errorMessage}
          </p>
        )}
        <button type="submit" disabled={submitting} style={{ padding: '0.5rem 1rem' }}>
          {submitting ? 'ログイン中…' : 'ログイン'}
        </button>
      </form>
    </main>
  );
}
