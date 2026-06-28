/**
 * CycleStartPage の振る舞いテスト（Phase 10.5）。
 *
 * 観点：
 *   - 起動ボタン押下時、デフォルトで「全員」チェック ON → mode=ALL で create 呼出。
 *   - チェックを外して起動 → mode=UNREACHABLE_ONLY で create 呼出。
 *   - Idempotency-Key が DI されたファクトリから取得される。
 *   - Retry_Count / Retry_Interval は変更不可（チェックボックス以外のフォーム入力は無い）。
 *   - 起動成功時、画面に cycleId と dictionaryVersion が表示される。
 *   - idempotentReplay=true の応答は「既存サイクルが返されました」の見出しで表示。
 *   - 400 / 409 / 500 はサーバーメッセージを画面に表示する。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { CycleApiError, type CycleClient, type CreateCycleResult } from '../api/cycleClient';

import { CycleStartPage } from './CycleStartPage';

function makeClient(overrides: Partial<CycleClient>): CycleClient {
  const fail = (n: string) => (): never => {
    throw new Error(`unexpected client.${n}`);
  };
  const fake = {
    create: vi.fn(fail('create')),
    ...overrides,
  };
  return fake as unknown as CycleClient;
}

describe('CycleStartPage', () => {
  it('初期状態で「全員」チェックボックスが ON である', () => {
    const client = makeClient({});
    render(
      <MemoryRouter>
        <CycleStartPage client={client} idempotencyKeyFactory={() => 'idem-init'} />
      </MemoryRouter>,
    );
    const checkbox = screen.getByRole('checkbox', { name: '全員' });
    expect(checkbox).toBeChecked();
  });

  it('チェック ON で起動すると mode=ALL かつ Idempotency-Key 付きで create が呼ばれる', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<CreateCycleResult> =>
        Promise.resolve({
          cycleId: 'cyc-success',
          status: 'RUNNING',
          startedAt: '2026-06-25T01:00:00Z',
          dictionaryVersion: 12,
          mode: 'ALL',
        }),
    );
    const idempotencyKeyFactory = vi.fn(() => 'idem-uuid-test');
    const client = makeClient({ create: createMock });

    render(
      <MemoryRouter>
        <CycleStartPage client={client} idempotencyKeyFactory={idempotencyKeyFactory} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: 'サイクルを起動する' }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith(
        { mode: 'ALL', retryCount: 3, retryIntervalMinutes: 5 },
        'idem-uuid-test',
      );
    });
    expect(idempotencyKeyFactory).toHaveBeenCalledTimes(1);
    // 結果表示。
    expect(await screen.findByTestId('cycle-result-cycle-id')).toHaveTextContent('cyc-success');
    expect(screen.getByTestId('cycle-result-dictionary-version')).toHaveTextContent('12');
    expect(screen.getByTestId('cycle-result-status')).toHaveTextContent('RUNNING');
  });

  it('チェック OFF で起動すると mode=UNREACHABLE_ONLY で create が呼ばれる', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<CreateCycleResult> =>
        Promise.resolve({
          cycleId: 'cyc-unreach',
          status: 'RUNNING',
          startedAt: '2026-06-25T02:00:00Z',
          dictionaryVersion: 13,
          mode: 'UNREACHABLE_ONLY',
        }),
    );
    const client = makeClient({ create: createMock });

    render(
      <MemoryRouter>
        <CycleStartPage client={client} idempotencyKeyFactory={() => 'idem-unreach'} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('checkbox', { name: '全員' })); // ON → OFF
    await user.click(screen.getByRole('button', { name: 'サイクルを起動する' }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith(
        { mode: 'UNREACHABLE_ONLY', retryCount: 3, retryIntervalMinutes: 5 },
        'idem-unreach',
      );
    });
  });

  it('Retry_Count / Retry_Interval は固定値で表示専用（フォーム入力は無い）', () => {
    const client = makeClient({});
    render(
      <MemoryRouter>
        <CycleStartPage client={client} idempotencyKeyFactory={() => 'idem-display'} />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('cycle-retry-count')).toHaveTextContent('3');
    expect(screen.getByTestId('cycle-retry-interval')).toHaveTextContent('5');

    // テキストボックス / 数値入力フィールドは存在しない（チェックボックスのみがインタラクティブ）。
    expect(screen.queryByRole('spinbutton')).toBeNull();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('idempotentReplay=true の応答は専用見出しで表示される', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<CreateCycleResult> =>
        Promise.resolve({
          cycleId: 'cyc-replay',
          status: 'RUNNING',
          startedAt: '2026-06-25T03:00:00Z',
          dictionaryVersion: 7,
          idempotentReplay: true,
        }),
    );
    const client = makeClient({ create: createMock });

    render(
      <MemoryRouter>
        <CycleStartPage client={client} idempotencyKeyFactory={() => 'idem-replay'} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: 'サイクルを起動する' }));

    expect(
      await screen.findByRole('heading', {
        name: '同じ Idempotency-Key で既存サイクルが返されました',
      }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('cycle-result-cycle-id')).toHaveTextContent('cyc-replay');
  });

  it('409 二重起動はサーバーメッセージを画面に表示する', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<CreateCycleResult> =>
        Promise.reject(new CycleApiError(409, 'Another cycle is already RUNNING')),
    );
    const client = makeClient({ create: createMock });

    render(
      <MemoryRouter>
        <CycleStartPage client={client} idempotencyKeyFactory={() => 'idem-dup'} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: 'サイクルを起動する' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Another cycle is already RUNNING');
    expect(screen.queryByTestId('cycle-result-cycle-id')).toBeNull();
  });

  it('400 辞書空エラーもサーバーメッセージを画面に表示する', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<CreateCycleResult> =>
        Promise.reject(
          new CycleApiError(
            400,
            'Active dictionary is empty. Add at least one keyword before starting a cycle.',
          ),
        ),
    );
    const client = makeClient({ create: createMock });

    render(
      <MemoryRouter>
        <CycleStartPage client={client} idempotencyKeyFactory={() => 'idem-empty'} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', { name: 'サイクルを起動する' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Active dictionary is empty');
  });
});
