/**
 * SPA ルートのルーティング定義。
 *
 * 構造：
 *   /login        : ログインページ（無認証で到達可能）
 *   /forbidden    : 403 ページ（無認証で到達可能）
 *   /             : AuthGuard 経由で AdminLayout 配下を表示
 *     /           : AdminHome
 *     その他      : "/" へリダイレクト（Phase 10.4 以降に画面追加予定）
 *
 * 設計判断：
 *   - `BrowserRouter` 採用。S3 + CloudFront 配信時の SPA fallback
 *     （403/404 → /index.html）は Phase 11 で CloudFront 側に組み込む。
 *   - テスト容易化のため、ルート定義部分 `AppRoutes` と `BrowserRouter`
 *     ラッパー `AppRouter` を分離する。テストでは `MemoryRouter` から
 *     `<AppRoutes />` を直接マウントできる。
 */

import type { JSX } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import {
  CycleDetailPage,
  CycleStartPage,
  CycleStatusPage,
  CyclesListPage,
  TranscriptViewerPage,
} from '../cycles';
import { DictionaryManagementPage } from '../dictionary';
import { EmployeeCsvImportPage, EmployeeFormPage, EmployeeListPage } from '../employees';
import { InboundListPage, InboundTranscriptViewerPage } from '../inbound';

import { AdminHome, AdminLayout } from './AdminLayout';
import { AuthGuard } from './AuthGuard';
import { ForbiddenPage } from './ForbiddenPage';
import { LoginPage } from './LoginPage';
import { NewPasswordPage } from './NewPasswordPage';
import { SessionExpiredListener } from './SessionExpiredListener';

export function AppRoutes(): JSX.Element {
  return (
    <>
      <SessionExpiredListener />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/new-password" element={<NewPasswordPage />} />
        <Route path="/forbidden" element={<ForbiddenPage />} />
        <Route element={<AuthGuard />}>
          <Route element={<AdminLayout />}>
            <Route index element={<AdminHome />} />
            <Route path="employees" element={<EmployeeListPage />} />
            <Route path="employees/new" element={<EmployeeFormPage />} />
            <Route path="employees/:id/edit" element={<EmployeeFormPage />} />
            <Route path="employees/import" element={<EmployeeCsvImportPage />} />
            <Route path="cycles/new" element={<CycleStartPage />} />
            <Route path="cycles" element={<CyclesListPage />} />
            <Route path="cycles/:cycleId" element={<CycleDetailPage />} />
            <Route path="cycles/:cycleId/status" element={<CycleStatusPage />} />
            <Route
              path="cycles/:cycleId/transcripts/:employeeId/:seq"
              element={<TranscriptViewerPage />}
            />
            <Route path="inbound" element={<InboundListPage />} />
            <Route path="inbound/:contactId/transcript" element={<InboundTranscriptViewerPage />} />
            <Route path="dictionary" element={<DictionaryManagementPage />} />
            {/* 未定義ルートはダッシュボードへ戻す（Phase 10.4 以降で追加予定）。 */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Route>
      </Routes>
    </>
  );
}

export function AppRouter(): JSX.Element {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
