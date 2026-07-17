/**
 * 管理者画面のレイアウト。
 *
 * 本コンポーネントは AuthGuard 通過後にのみ描画される。
 * 各業務画面（社員マスタ管理 / サイクル起動 / ステータス閲覧 / 辞書管理）
 * は子ルートとして配置される。
 *
 * 設計判断：
 *   - ヘッダにログアウトボタンを置き、UI から確実にセッションを破棄できる
 *     動線を提供する（Requirement 1.9 と整合：管理者専用 UI の閉じ方を確保）。
 *   - 全画面で一貫した「ダッシュボードへ戻る」ナビをヘッダ右側に置く
 *     （ε-1 ナビ一貫性修正、15.2a 実機検証で発覚した UX 不備への対応）。
 *     共通レイアウトに置くことで全管理画面に DRY に波及する（19 原則 (a)）。
 *   - 子ルートが未追加でも単独でレンダリングできるよう、Outlet と本体の
 *     プレースホルダ表示を併置する。
 */

import { useCallback, type JSX } from 'react';
import { Link, Outlet, useNavigate } from 'react-router-dom';

import { getAuthProvider } from '../auth';
import type { AuthSessionProvider } from '../auth/types';

export interface AdminLayoutProps {
  /** テスト用 DI。 */
  readonly authProvider?: AuthSessionProvider;
}

export function AdminLayout({ authProvider }: AdminLayoutProps = {}): JSX.Element {
  const navigate = useNavigate();
  const provider = authProvider ?? getAuthProvider();

  const onSignOut = useCallback(() => {
    void (async () => {
      await provider.signOut();
      navigate('/login', { replace: true });
    })();
  }, [provider, navigate]);

  return (
    <div className="container">
      <header>
        <nav>
          <ul>
            <li>
              <strong>安否確認システム 管理サイト</strong>
            </li>
          </ul>
          <ul>
            <li>
              <Link to="/" data-testid="admin-layout-home-link">
                <button type="button" className="outline">ダッシュボードへ戻る</button>
              </Link>
            </li>
            <li>
              <button type="button" onClick={onSignOut} data-testid="admin-layout-signout" className="outline secondary">
                ログアウト
              </button>
            </li>
          </ul>
        </nav>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}

/**
 * Phase 10.3 時点では業務画面が未着手のため、認可成功時に表示する
 * 初期画面を提供する。Phase 10.4 以降の各機能画面へのリンクを集約する
 * ダッシュボードとして拡張していく。
 */
export function AdminHome(): JSX.Element {
  return (
    <section>
      <h1>ダッシュボード</h1>
      <p>管理者として認証されました。</p>
      <ul>
        <li>
          <Link to="/employees">社員マスタ管理</Link>
        </li>
        <li>
          <Link to="/cycles/new">安否確認 起動</Link>
        </li>
        <li>
          <Link to="/cycles">安否確認 履歴</Link>
        </li>
        <li>
          <Link to="/inbound">着信履歴</Link>
        </li>
        <li>
          <Link to="/dictionary">キーワード辞書管理</Link>
        </li>
      </ul>
    </section>
  );
}
