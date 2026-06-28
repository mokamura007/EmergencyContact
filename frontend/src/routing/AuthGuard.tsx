/**
 * 管理者ロール限定ルーティングのガードコンポーネント。
 *
 * 対応要件：
 *   - Requirement 1.3 : 管理者ロールが含まれる場合に管理機能を表示する。
 *   - Requirement 1.4 : 管理者ロールが含まれない場合は全機能を拒否する。
 *   - Requirement 1.9 : 一般社員ロールは存在しないため、`Administrator` 以外は
 *                       全て拒否する（403）。
 *   - Property 1      : `isAdministrator` が true のときのみ Outlet を描画する。
 *
 * 振る舞い：
 *   1. マウント時に `AuthSessionProvider.getCurrentSession()` でセッション解決
 *      （内部で refresh token による自動更新が走る）。
 *   2. セッション null  → `/login` へリダイレクト（`from` を state に保持）。
 *   3. セッション有り + ID トークン解析失敗 / `Administrator` 未所属 → `/forbidden` へリダイレクト。
 *   4. セッション有り + `Administrator` 所属 → `Outlet`（子ルート）を描画。
 *
 * 設計判断：
 *   - 認可判定は SPA 側の UI 表示制御目的のみ。API 呼出に対する最終判定は
 *     サーバー側の Cognito Authorizer + Lambda 内クレーム検証が担う
 *     （design.md "Auth_Service / ロール判定" 参照）。
 *   - `SessionExpiredError` を含む取得失敗時は `/login` に倒す（再ログイン
 *     誘導は `sessionExpiredEvent` 経由でも別途行われるが、ガード自身も
 *     冪等に未認証扱いするだけで二重通知の害は無い）。
 *   - JWT decode 失敗時は `/forbidden` 側に倒す。これは「トークンは取得
 *     できているが内容が壊れている」状態であり、ログアウト誘導ではなく
 *     構成異常として 403 を見せる方が安全（攻撃的に細工されたトークンの
 *     場合も含めて、管理機能を絶対に出さない）。
 */

import { useEffect, useState, type JSX, type ReactNode } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { getAuthProvider } from '../auth';
import { SessionExpiredError } from '../auth/errors';
import { decodeJwtPayload, extractCognitoGroups, isAdministrator } from '../auth/roles';
import type { AuthSessionProvider } from '../auth/types';

type GuardState =
  | { readonly kind: 'loading' }
  | { readonly kind: 'unauthenticated' }
  | { readonly kind: 'forbidden' }
  | { readonly kind: 'authorized' };

export interface AuthGuardProps {
  /**
   * 認証プロバイダ。テスト時に DI でフェイクを差し込むために露出する。
   * 本番ではシングルトン `getAuthProvider()` を利用する。
   */
  readonly authProvider?: AuthSessionProvider;
  /**
   * ローディング中に描画する要素。明示的に差し替えたい場合のみ指定する。
   */
  readonly loadingFallback?: ReactNode;
}

export function AuthGuard({ authProvider, loadingFallback }: AuthGuardProps = {}): JSX.Element {
  const location = useLocation();
  const provider = authProvider ?? getAuthProvider();
  const [state, setState] = useState<GuardState>({ kind: 'loading' });

  useEffect(() => {
    const ac = new AbortController();

    void (async () => {
      let next: GuardState;
      try {
        const session = await provider.getCurrentSession();
        if (session === null) {
          next = { kind: 'unauthenticated' };
        } else {
          try {
            const claims = decodeJwtPayload(session.idToken);
            const groups = extractCognitoGroups(claims);
            next = { kind: isAdministrator(groups) ? 'authorized' : 'forbidden' };
          } catch (decodeErr) {
            // トークンは取得できているが内容が壊れている。403 側に倒す。
            console.error('[AuthGuard] failed to decode ID token claims', decodeErr);
            next = { kind: 'forbidden' };
          }
        }
      } catch (err) {
        if (err instanceof SessionExpiredError) {
          next = { kind: 'unauthenticated' };
        } else {
          // 想定外エラーは UI 上「未認証」に倒したうえで詳細をログに残す。
          // SPA 側からは復旧手段が無いため、ユーザーには再ログインを促す。
          console.error('[AuthGuard] unexpected error while resolving session', err);
          next = { kind: 'unauthenticated' };
        }
      }

      if (!ac.signal.aborted) {
        setState(next);
      }
    })();

    return () => {
      ac.abort();
    };
  }, [provider]);

  if (state.kind === 'loading') {
    return (
      <div role="status" aria-live="polite">
        {loadingFallback ?? '読み込み中…'}
      </div>
    );
  }
  if (state.kind === 'unauthenticated') {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (state.kind === 'forbidden') {
    return <Navigate to="/forbidden" replace />;
  }
  return <Outlet />;
}
