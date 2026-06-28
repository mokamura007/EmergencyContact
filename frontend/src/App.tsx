import type { JSX } from 'react';

import { AppRouter } from './routing';

/**
 * 管理者向け SPA のルートコンポーネント。
 *
 * Phase 10.3 以降、ルーティングと管理者ロール判定（AuthGuard）を備えた
 * `<AppRouter />` を唯一の子要素として描画する。各機能画面は Phase 10.4
 * 以降で `routing/AppRouter.tsx` の子ルートに追加される。
 */
export function App(): JSX.Element {
  return <AppRouter />;
}
