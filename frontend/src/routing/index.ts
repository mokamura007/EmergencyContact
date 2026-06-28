/**
 * ルーティング層の公開窓口（barrel）。
 *
 * SPA エントリ（main.tsx / App.tsx）はここから `AppRouter` のみを参照する。
 * 各画面コンポーネントはテスト時に直接参照することがあるためエクスポート対象に含める。
 */

export { AppRouter, AppRoutes } from './AppRouter';
export { AuthGuard, type AuthGuardProps } from './AuthGuard';
export { ForbiddenPage, type ForbiddenPageProps } from './ForbiddenPage';
export { LoginPage, type LoginPageProps } from './LoginPage';
export { AdminLayout, AdminHome, type AdminLayoutProps } from './AdminLayout';
export { SessionExpiredListener } from './SessionExpiredListener';
