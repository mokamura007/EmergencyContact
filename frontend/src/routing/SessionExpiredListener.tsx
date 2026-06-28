/**
 * セッション失効イベントの購読 → 再ログイン誘導コンポーネント。
 *
 * 役割：
 *   - 認証層（`httpClient` / `CognitoAuthProvider`）が
 *     `sessionExpiredEvent.notifySessionExpired()` を発火した際に、
 *     UI 層が反応して `/login` へ遷移する。
 *
 * 設計判断：
 *   - 認証層は UI に依存しない（DRY、循環参照回避）。リダイレクト操作は
 *     必ず本コンポーネントを通して React Router の API で行う。
 *   - 既に `/login` / `/forbidden` に居る場合は遷移しない（不要なナビゲーション
 *     抑止と、ユーザー入力中フォームの巻戻し防止）。
 *   - 19原則(b)：subscribe / unsubscribe は副作用として正しく後始末する
 *     （アンマウント時に確実に解除し、ハンドラの多重発火を防ぐ）。
 */

import { useEffect, type JSX } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { subscribeSessionExpired } from '../auth/sessionExpiredEvent';

const SKIP_PATHS = new Set<string>(['/login', '/forbidden']);

export function SessionExpiredListener(): JSX.Element | null {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const unsubscribe = subscribeSessionExpired(() => {
      if (SKIP_PATHS.has(location.pathname)) {
        return;
      }
      navigate('/login', {
        replace: true,
        state: { from: location.pathname },
      });
    });
    return unsubscribe;
  }, [navigate, location.pathname]);

  return null;
}
