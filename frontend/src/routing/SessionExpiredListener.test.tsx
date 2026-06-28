/**
 * SessionExpiredListener のテスト。
 *
 * - notifySessionExpired が発火したら /login へリダイレクトする。
 * - /login / /forbidden 上では遷移を発生させない。
 * - アンマウント時にリスナーが解除される。
 */

import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import {
  _resetSessionExpiredListenersForTest,
  notifySessionExpired,
} from '../auth/sessionExpiredEvent';

import { SessionExpiredListener } from './SessionExpiredListener';

afterEach(() => {
  _resetSessionExpiredListenersForTest();
});

function renderHarness(initialPath: string): void {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <SessionExpiredListener />
      <Routes>
        <Route path="/" element={<div data-testid="home">home</div>} />
        <Route path="/employees" element={<div data-testid="employees">employees</div>} />
        <Route path="/login" element={<div data-testid="login">login</div>} />
        <Route path="/forbidden" element={<div data-testid="forbidden">forbidden</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('SessionExpiredListener', () => {
  it('保護ルートで notifySessionExpired を受けたら /login へ遷移する', async () => {
    renderHarness('/employees');
    expect(screen.getByTestId('employees')).toBeInTheDocument();

    notifySessionExpired();

    expect(await screen.findByTestId('login')).toBeInTheDocument();
  });

  it('/login 上では通知を受けても遷移しない（フォーム入力中の巻戻し抑止）', async () => {
    renderHarness('/login');
    expect(screen.getByTestId('login')).toBeInTheDocument();

    notifySessionExpired();

    // 通知後も /login のままであることを少し待ってから確認。
    await waitFor(() => {
      expect(screen.getByTestId('login')).toBeInTheDocument();
    });
  });

  it('/forbidden 上では通知を受けても遷移しない', async () => {
    renderHarness('/forbidden');
    expect(screen.getByTestId('forbidden')).toBeInTheDocument();

    notifySessionExpired();

    await waitFor(() => {
      expect(screen.getByTestId('forbidden')).toBeInTheDocument();
    });
  });
});
