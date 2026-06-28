/**
 * DictionaryManagementPage の振る舞いテスト（Phase 10.9、Requirement 8.1〜8.4 / 8.7）。
 *
 * 観点：
 *   - 初期表示で 3 カテゴリ + バージョン番号が描画される。
 *   - 追加成功 → 再取得 → 追加されたキーワードが表示される。
 *   - 無効化（DELETE）成功 → 再取得 → 該当キーワードが消える。
 *   - touch（PATCH）成功 → 再取得 → バージョン番号が増加。
 *   - 409 Conflict 時：バナー表示 + 自動再取得（最新 version が表示される）。
 *   - API エラー時：serverMessage が画面に表示される。
 *   - 空辞書の初期表示。
 *   - busy 制御：操作中はボタン全部が disabled。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import {
  DictionaryApiError,
  DictionaryConflictError,
  type DictionaryClient,
  type DictionarySnapshot,
} from '../api/dictionaryClient';

import { DictionaryManagementPage } from './DictionaryManagementPage';

function snap(
  version: number,
  overrides: Partial<DictionarySnapshot['categories']> = {},
): DictionarySnapshot {
  return {
    version,
    categories: {
      SAFE: [],
      INJURED: [],
      UNAVAILABLE: [],
      ...overrides,
    },
  };
}

function makeClient(behaviour: {
  readonly listResponses: readonly DictionarySnapshot[];
  readonly add?: (
    category: string,
    keyword: string,
    expected: number,
  ) => Promise<{ version: number }>;
  readonly remove?: (
    category: string,
    keyword: string,
    expected: number,
  ) => Promise<{ version: number }>;
  readonly touch?: (
    category: string,
    keyword: string,
    expected: number,
  ) => Promise<{ version: number }>;
}): {
  readonly client: DictionaryClient;
  readonly list: ReturnType<typeof vi.fn>;
  readonly add: ReturnType<typeof vi.fn>;
  readonly remove: ReturnType<typeof vi.fn>;
  readonly touch: ReturnType<typeof vi.fn>;
} {
  // list は呼出回数に応じて配列の頭から消費する（最後の値を再利用）
  const responses = [...behaviour.listResponses];
  const list = vi.fn((): Promise<DictionarySnapshot> => {
    const next = responses.length > 1 ? responses.shift() : responses[0];
    if (next === undefined) {
      return Promise.reject(new Error('No more list responses configured for this test'));
    }
    return Promise.resolve(next);
  });
  const add = vi.fn(
    behaviour.add ?? ((): Promise<{ version: number }> => Promise.resolve({ version: 0 })),
  );
  const remove = vi.fn(
    behaviour.remove ?? ((): Promise<{ version: number }> => Promise.resolve({ version: 0 })),
  );
  const touch = vi.fn(
    behaviour.touch ?? ((): Promise<{ version: number }> => Promise.resolve({ version: 0 })),
  );
  return {
    client: { list, add, remove, touch } as unknown as DictionaryClient,
    list,
    add,
    remove,
    touch,
  };
}

describe('DictionaryManagementPage', () => {
  it('初期表示で 3 カテゴリとバージョン番号が表示される', async () => {
    const { client } = makeClient({
      listResponses: [
        snap(5, {
          SAFE: ['無事', '元気'],
          INJURED: ['怪我'],
        }),
      ],
    });

    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-version')).toHaveTextContent('5');
    });
    expect(screen.getByTestId('dictionary-category-SAFE')).toBeInTheDocument();
    expect(screen.getByTestId('dictionary-category-INJURED')).toBeInTheDocument();
    expect(screen.getByTestId('dictionary-category-UNAVAILABLE')).toBeInTheDocument();
    expect(screen.getByTestId('dictionary-row-SAFE-無事')).toBeInTheDocument();
    expect(screen.getByTestId('dictionary-row-SAFE-元気')).toBeInTheDocument();
    expect(screen.getByTestId('dictionary-row-INJURED-怪我')).toBeInTheDocument();
    expect(screen.getByTestId('dictionary-empty-UNAVAILABLE')).toBeInTheDocument();
  });

  it('空辞書ではバージョン 0 と各カテゴリの empty 表示', async () => {
    const { client } = makeClient({ listResponses: [snap(0)] });
    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('dictionary-version')).toHaveTextContent('0');
    });
    expect(screen.getByTestId('dictionary-empty-SAFE')).toBeInTheDocument();
    expect(screen.getByTestId('dictionary-empty-INJURED')).toBeInTheDocument();
    expect(screen.getByTestId('dictionary-empty-UNAVAILABLE')).toBeInTheDocument();
  });

  it('追加成功時にキーワードが画面に表示され、バージョンが上がる', async () => {
    const { client, add, list } = makeClient({
      listResponses: [snap(1), snap(2, { SAFE: ['無事'] })],
      add: () => Promise.resolve({ version: 2 }),
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-version')).toHaveTextContent('1');
    });

    const input = screen.getByTestId('dictionary-add-input-SAFE');
    await user.type(input, '無事');
    await user.click(screen.getByTestId('dictionary-add-button-SAFE'));

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-row-SAFE-無事')).toBeInTheDocument();
    });
    expect(screen.getByTestId('dictionary-version')).toHaveTextContent('2');
    expect(add).toHaveBeenCalledWith('SAFE', '無事', 1);
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('無効化(DELETE)成功で対象キーワードが消え、バージョンが上がる', async () => {
    const { client, remove } = makeClient({
      listResponses: [snap(5, { SAFE: ['無事'] }), snap(6)],
      remove: () => Promise.resolve({ version: 6 }),
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-row-SAFE-無事')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('dictionary-remove-SAFE-無事'));

    await waitFor(() => {
      expect(screen.queryByTestId('dictionary-row-SAFE-無事')).toBeNull();
    });
    expect(screen.getByTestId('dictionary-version')).toHaveTextContent('6');
    expect(remove).toHaveBeenCalledWith('SAFE', '無事', 5);
  });

  it('touch(PATCH)成功でバージョン番号だけが上がる', async () => {
    const { client, touch } = makeClient({
      listResponses: [snap(10, { SAFE: ['無事'] }), snap(11, { SAFE: ['無事'] })],
      touch: () => Promise.resolve({ version: 11 }),
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-version')).toHaveTextContent('10');
    });

    await user.click(screen.getByTestId('dictionary-touch-SAFE-無事'));

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-version')).toHaveTextContent('11');
    });
    expect(screen.getByTestId('dictionary-row-SAFE-無事')).toBeInTheDocument();
    expect(touch).toHaveBeenCalledWith('SAFE', '無事', 10);
  });

  it('409 Conflict 時：バナーを表示し最新の状態で再描画する', async () => {
    const { client, list } = makeClient({
      listResponses: [
        snap(3, { SAFE: ['無事'] }),
        snap(7, { SAFE: ['無事', '元気'] }), // 他管理者が更新した最新
      ],
      remove: () =>
        Promise.reject(
          new DictionaryConflictError(
            'Concurrent modification detected; refresh the dictionary version and retry',
            null,
          ),
        ),
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-version')).toHaveTextContent('3');
    });

    await user.click(screen.getByTestId('dictionary-remove-SAFE-無事'));

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-conflict-banner')).toBeInTheDocument();
    });
    expect(screen.getByTestId('dictionary-version')).toHaveTextContent('7');
    expect(screen.getByTestId('dictionary-row-SAFE-元気')).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('500 API エラー時に serverMessage を表示する', async () => {
    const { client } = makeClient({
      listResponses: [snap(1, { SAFE: ['無事'] }), snap(1, { SAFE: ['無事'] })],
      touch: () => Promise.reject(new DictionaryApiError(500, 'DDB write failed')),
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-row-SAFE-無事')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('dictionary-touch-SAFE-無事'));

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-error')).toHaveTextContent('HTTP 500');
    });
    expect(screen.getByTestId('dictionary-error')).toHaveTextContent('DDB write failed');
  });

  it('操作中(busy)はボタンが disabled になる（連打防止）', async () => {
    const deferred: {
      resolve?: (value: { version: number }) => void;
    } = {};
    const { client } = makeClient({
      listResponses: [snap(5, { SAFE: ['無事'] }), snap(6)],
      remove: () =>
        new Promise<{ version: number }>((resolve) => {
          deferred.resolve = resolve;
        }),
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-row-SAFE-無事')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('dictionary-remove-SAFE-無事'));

    // busy 中はボタンが disabled
    await waitFor(() => {
      expect(screen.getByTestId('dictionary-touch-SAFE-無事')).toBeDisabled();
    });
    expect(screen.getByTestId('dictionary-remove-SAFE-無事')).toBeDisabled();
    expect(screen.getByTestId('dictionary-add-button-SAFE')).toBeDisabled();

    // 解放後は再度有効化される
    deferred.resolve?.({ version: 6 });
    await waitFor(() => {
      expect(screen.queryByTestId('dictionary-row-SAFE-無事')).toBeNull();
    });
    expect(screen.getByTestId('dictionary-add-button-SAFE')).toBeEnabled();
  });

  it('初回 list() が失敗するとエラーバナーを表示', async () => {
    const list = vi.fn().mockRejectedValue(new DictionaryApiError(500, 'scan failed'));
    const client = {
      list,
      add: vi.fn(),
      remove: vi.fn(),
      touch: vi.fn(),
    } as unknown as DictionaryClient;

    render(
      <MemoryRouter>
        <DictionaryManagementPage dictionaryClient={client} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('dictionary-error')).toHaveTextContent('scan failed');
    });
  });
});
