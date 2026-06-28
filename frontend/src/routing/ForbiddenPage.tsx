/**
 * 403 Forbidden ページ。
 *
 * 対応要件：
 *   - Requirement 1.4 : 管理者ロールが含まれない場合は全機能へのアクセスを拒否する。
 *   - Requirement 1.9 : 一般社員ロールは提供しない（本ページは「権限なし」を
 *                       明示するための単一の到達点）。
 *
 * 設計判断：
 *   - ナビゲーション要素は最小限とし、ログアウト → ログイン画面への遷移
 *     のみを提供する。これにより別アカウントでの再ログインが容易になる。
 *   - 監査ログ等の副作用は持たない（サーバー側の認可ログで充分なため、
 *     19原則(a) DRY に従い UI 側では発火させない）。
 */

import { useCallback, type JSX } from 'react';
import { useNavigate } from 'react-router-dom';

import { getAuthProvider } from '../auth';
import type { AuthSessionProvider } from '../auth/types';

export interface ForbiddenPageProps {
  /** テスト用 DI。本番では `getAuthProvider()` のシングルトンを使う。 */
  readonly authProvider?: AuthSessionProvider;
}

export function ForbiddenPage({ authProvider }: ForbiddenPageProps = {}): JSX.Element {
  const navigate = useNavigate();
  const provider = authProvider ?? getAuthProvider();

  const onSignOut = useCallback(() => {
    void (async () => {
      await provider.signOut();
      navigate('/login', { replace: true });
    })();
  }, [provider, navigate]);

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: '2rem' }}>
      <h1>403 Forbidden</h1>
      <p>このページにアクセスする権限がありません。</p>
      <p>
        本システムは <code>Administrator</code> グループに所属する管理者のみが
        利用できます。別アカウントでログインし直す場合は下のボタンを押してください。
      </p>
      <button type="button" onClick={onSignOut}>
        ログアウトしてログイン画面へ
      </button>
    </main>
  );
}
