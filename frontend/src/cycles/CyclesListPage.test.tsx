/**
 * CyclesListPage の振る舞いテスト（Phase 10.7、Requirement 12.1）。
 *
 * 観点：
 *   - 取得後に Cycle 行が startedAt 降順で表示される（バックエンド契約に従う）。
 *   - 50 件単位のページング（ページ送りボタンで切替）。
 *   - 空のとき「過去のサイクルはまだありません」表示。
 *   - API エラーで `serverMessage` を含むエラーバナー表示。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { CycleApiError, type CycleClient, type CycleSummary } from '../api/cycleClient';

import { CyclesListPage } from './CyclesListPage';

function makeSummary(idx: number): CycleSummary {
  // idx が大きいほど新しい起動時刻（降順表示確認用）。
  return {
    cycleId: `cyc-${idx.toString().padStart(3, '0')}`,
    status: 'COMPLETED',
    mode: 'ALL',
    startedAt: new Date(2026, 0, 1 + idx).toISOString(),
    completedAt: new Date(2026, 0, 1 + idx, 1).toISOString(),
    dictionaryVersion: idx,
  };
}

function makeClient(impl: () => Promise<{ cycles: readonly CycleSummary[]; total: number }>): {
  client: CycleClient;
  list: ReturnType<typeof vi.fn>;
} {
  const list = vi.fn(impl);
  return { client: { list } as unknown as CycleClient, list };
}

describe('CyclesListPage', () => {
  it('Cycle 行を取得して表示し、空でないことを確認', async () => {
    const cycles = [makeSummary(2), makeSummary(1)]; // backend は降順を返す前提
    const { client } = makeClient(() => Promise.resolve({ cycles, total: 2 }));

    render(
      <MemoryRouter>
        <CyclesListPage client={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('cycle-row-cyc-002')).toBeInTheDocument();
    });
    expect(screen.getByTestId('cycle-row-cyc-001')).toBeInTheDocument();
    // 1 ページに収まるのでページ送りは無効。
    expect(screen.getByTestId('cycles-next-page')).toBeDisabled();
    expect(screen.getByTestId('cycles-prev-page')).toBeDisabled();
  });

  it('表示順は cycles 配列の順序を保持する（先頭が新しい起動時刻、降順）', async () => {
    const cycles = [makeSummary(5), makeSummary(3), makeSummary(1)];
    const { client } = makeClient(() => Promise.resolve({ cycles, total: 3 }));

    render(
      <MemoryRouter>
        <CyclesListPage client={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('cycles-table')).toBeInTheDocument();
    });
    const rows = screen.getAllByTestId(/^cycle-row-/);
    expect(rows[0]).toHaveAttribute('data-testid', 'cycle-row-cyc-005');
    expect(rows[1]).toHaveAttribute('data-testid', 'cycle-row-cyc-003');
    expect(rows[2]).toHaveAttribute('data-testid', 'cycle-row-cyc-001');
  });

  it('50 件超は 1 ページに 50 件、次ページボタンで残りを表示', async () => {
    const cycles: CycleSummary[] = [];
    for (let i = 75; i >= 1; i--) cycles.push(makeSummary(i));
    const { client } = makeClient(() => Promise.resolve({ cycles, total: 75 }));

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <CyclesListPage client={client} pageSize={50} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('cycles-table')).toBeInTheDocument();
    });

    // 1 ページ目：75 個中 50 行（cyc-075 〜 cyc-026）。
    expect(screen.getByTestId('cycle-row-cyc-075')).toBeInTheDocument();
    expect(screen.getByTestId('cycle-row-cyc-026')).toBeInTheDocument();
    expect(screen.queryByTestId('cycle-row-cyc-025')).toBeNull();
    expect(screen.getByTestId('cycles-pagination-summary')).toHaveTextContent('1 - 50');

    // 次ページ：残り 25 件（cyc-025 〜 cyc-001）。
    await user.click(screen.getByTestId('cycles-next-page'));
    await waitFor(() => {
      expect(screen.getByTestId('cycle-row-cyc-025')).toBeInTheDocument();
    });
    expect(screen.getByTestId('cycle-row-cyc-001')).toBeInTheDocument();
    expect(screen.queryByTestId('cycle-row-cyc-026')).toBeNull();
    expect(screen.getByTestId('cycles-pagination-summary')).toHaveTextContent('51 - 75');
    expect(screen.getByTestId('cycles-next-page')).toBeDisabled();

    // 前ページに戻ると 1 ページ目相当が表示される。
    await user.click(screen.getByTestId('cycles-prev-page'));
    await waitFor(() => {
      expect(screen.getByTestId('cycle-row-cyc-075')).toBeInTheDocument();
    });
  });

  it('空応答のときは「過去のサイクルはまだありません」と表示', async () => {
    const { client } = makeClient(() => Promise.resolve({ cycles: [], total: 0 }));
    render(
      <MemoryRouter>
        <CyclesListPage client={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('cycles-empty')).toBeInTheDocument();
    });
  });

  it('API エラーで serverMessage を含むエラーバナー表示', async () => {
    const { client } = makeClient(() => Promise.reject(new CycleApiError(500, 'Internal error')));
    render(
      <MemoryRouter>
        <CyclesListPage client={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Internal error');
    });
  });
});
