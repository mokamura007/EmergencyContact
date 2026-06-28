/**
 * EmployeeListPage の振る舞いテスト。
 *
 * 観点：
 *   - マウント時に GET /employees を 1 回呼ぶ。
 *   - 結果を一覧表示する。
 *   - 「削除」ボタンで確認ダイアログを表示する。
 *   - 「削除する」確定で DELETE が呼ばれ、再読込が走る。
 *   - 「キャンセル」でダイアログが閉じ、削除 API は呼ばれない。
 *   - API エラー時にエラーメッセージが表示される（19原則(b)）。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { EmployeeClient, EmployeeSummary } from '../api/employeeClient';
import { EmployeeApiError } from '../api/employeeClient';

import { EmployeeListPage } from './EmployeeListPage';

function makeClient(overrides: Partial<EmployeeClient>): EmployeeClient {
  // 必要メソッドのみフェイク。未使用メソッドは throw で失敗を顕在化させる。
  const fail = (n: string) => (): never => {
    throw new Error(`unexpected client.${n}`);
  };
  const fake = {
    list: vi.fn((): Promise<EmployeeSummary[]> => Promise.resolve([])),
    get: vi.fn(fail('get')),
    create: vi.fn(fail('create')),
    update: vi.fn(fail('update')),
    remove: vi.fn(fail('remove')),
    removeCognitoUser: vi.fn(fail('removeCognitoUser')),
    importCsv: vi.fn(fail('importCsv')),
    ...overrides,
  };
  return fake as unknown as EmployeeClient;
}

function renderPage(client: EmployeeClient): void {
  render(
    <MemoryRouter initialEntries={['/employees']}>
      <EmployeeListPage client={client} />
    </MemoryRouter>,
  );
}

describe('EmployeeListPage', () => {
  it('マウント時に GET /employees を呼び、一覧を表示する', async () => {
    const listMock = vi.fn(
      (): Promise<EmployeeSummary[]> =>
        Promise.resolve([
          { employeeId: 'u1', name: '山田太郎', phoneNumber: '+819012345678', isAdmin: false },
          { employeeId: 'u2', name: '田中花子', phoneNumber: '+819011112222', isAdmin: true },
        ]),
    );
    const client = makeClient({ list: listMock });
    renderPage(client);
    expect(await screen.findByText('山田太郎')).toBeInTheDocument();
    expect(screen.getByText('田中花子')).toBeInTheDocument();
    expect(screen.getByText('+819011112222')).toBeInTheDocument();
    expect(listMock).toHaveBeenCalledTimes(1);
  });

  it('社員 0 件のとき空メッセージを表示する', async () => {
    const client = makeClient({
      list: vi.fn((): Promise<EmployeeSummary[]> => Promise.resolve([])),
    });
    renderPage(client);
    expect(await screen.findByText('社員レコードはまだ登録されていません。')).toBeInTheDocument();
  });

  it('削除ボタンで確認ダイアログを開き、削除確定で remove → list が呼ばれる', async () => {
    const user = userEvent.setup();
    const removeMock = vi.fn((): Promise<void> => Promise.resolve());
    let callCount = 0;
    const listMock = vi.fn((): Promise<EmployeeSummary[]> => {
      callCount += 1;
      return Promise.resolve(
        callCount === 1
          ? [{ employeeId: 'u1', name: '山田太郎', phoneNumber: '+8190', isAdmin: false }]
          : [],
      );
    });
    const client = makeClient({ list: listMock, remove: removeMock });
    renderPage(client);

    await user.click(await screen.findByRole('button', { name: '削除' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('削除の確認')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '削除する' }));

    await waitFor(() => {
      expect(removeMock).toHaveBeenCalledWith('u1');
    });
    await waitFor(() => {
      expect(listMock).toHaveBeenCalledTimes(2);
    });
    // ダイアログが閉じ、再読込で空表示。
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('キャンセルでダイアログが閉じ、remove は呼ばれない', async () => {
    const user = userEvent.setup();
    const removeMock = vi.fn((): Promise<void> => Promise.resolve());
    const client = makeClient({
      list: vi.fn(
        (): Promise<EmployeeSummary[]> =>
          Promise.resolve([
            { employeeId: 'u1', name: '山田太郎', phoneNumber: '+8190', isAdmin: false },
          ]),
      ),
      remove: removeMock,
    });
    renderPage(client);

    await user.click(await screen.findByRole('button', { name: '削除' }));
    await user.click(screen.getByRole('button', { name: 'キャンセル' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(removeMock).not.toHaveBeenCalled();
  });

  it('list が EmployeeApiError ならサーバーメッセージを表示する', async () => {
    const client = makeClient({
      list: vi.fn(
        (): Promise<EmployeeSummary[]> =>
          Promise.reject(new EmployeeApiError(500, 'database boom')),
      ),
    });
    renderPage(client);
    expect(await screen.findByRole('alert')).toHaveTextContent('database boom');
    expect(screen.getByRole('alert')).toHaveTextContent('HTTP 500');
  });
});

describe('EmployeeListPage — 論理削除済表示と Cognito 削除（Task 15.16）', () => {
  it('「論理削除済社員も表示」トグル ON で includeDeleted=true で再取得する', async () => {
    const user = userEvent.setup();
    const listMock = vi.fn((options?: { includeDeleted?: boolean }): Promise<EmployeeSummary[]> => {
      if (options?.includeDeleted === true) {
        return Promise.resolve([
          { employeeId: 'u1', name: '山田太郎', phoneNumber: '+8190', isAdmin: false },
          {
            employeeId: 'u9',
            name: '退職一郎',
            phoneNumber: '',
            isAdmin: true,
            deleted: true,
          },
        ]);
      }
      return Promise.resolve([
        { employeeId: 'u1', name: '山田太郎', phoneNumber: '+8190', isAdmin: false },
      ]);
    });
    const client = makeClient({ list: listMock });
    renderPage(client);

    // 初期：includeDeleted=false で 1 件のみ
    expect(await screen.findByText('山田太郎')).toBeInTheDocument();
    expect(screen.queryByText('退職一郎')).not.toBeInTheDocument();
    expect(listMock).toHaveBeenLastCalledWith({ includeDeleted: false });

    // トグル ON
    await user.click(screen.getByLabelText('論理削除済社員も表示'));

    expect(await screen.findByText('退職一郎')).toBeInTheDocument();
    expect(listMock).toHaveBeenLastCalledWith({ includeDeleted: true });
  });

  it('論理削除済かつ管理者ロールの行に Cognito 削除ボタンが表示される', async () => {
    const listMock = vi.fn(
      (): Promise<EmployeeSummary[]> =>
        Promise.resolve([
          {
            employeeId: 'u9',
            name: '退職一郎',
            phoneNumber: '',
            isAdmin: true,
            deleted: true,
          },
          {
            employeeId: 'u8',
            name: '退職二郎',
            phoneNumber: '',
            isAdmin: false,
            deleted: true,
          },
          {
            employeeId: 'u1',
            name: '現役太郎',
            phoneNumber: '+8190',
            isAdmin: true,
            deleted: false,
          },
        ]),
    );
    const client = makeClient({ list: listMock });
    renderPage(client);

    // 退職一郎（admin かつ deleted）→ Cognito 削除ボタンあり
    expect(await screen.findByText('退職一郎')).toBeInTheDocument();
    const cognitoButtons = screen.getAllByRole('button', { name: 'Cognito 削除' });
    expect(cognitoButtons).toHaveLength(1);

    // 退職二郎（non-admin かつ deleted）→ Cognito 削除ボタンなし、編集 / 削除も無し
    // → 「削除済」状態カラムは表示されているが、操作カラムは空
    // 現役太郎（admin かつ active）→ Cognito 削除ボタンなし
    const activeEditButton = screen.getByRole('button', { name: '編集' });
    expect(activeEditButton).toBeInTheDocument();
  });

  it('Cognito 削除ボタン → 確認ダイアログ → 確定で removeCognitoUser → list 再取得', async () => {
    const user = userEvent.setup();
    let callCount = 0;
    const listMock = vi.fn((): Promise<EmployeeSummary[]> => {
      callCount += 1;
      if (callCount === 1) {
        return Promise.resolve([
          {
            employeeId: 'u9',
            name: '退職一郎',
            phoneNumber: '',
            isAdmin: true,
            deleted: true,
          },
        ]);
      }
      return Promise.resolve([]);
    });
    const removeCognitoMock = vi.fn((): Promise<void> => Promise.resolve());
    const client = makeClient({ list: listMock, removeCognitoUser: removeCognitoMock });
    renderPage(client);

    await user.click(await screen.findByRole('button', { name: 'Cognito 削除' }));

    // 確認ダイアログが表示される
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Cognito アカウント削除の確認')).toBeInTheDocument();
    expect(screen.getByText(/この操作は元に戻せません。/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Cognito 削除する' }));

    await waitFor(() => {
      expect(removeCognitoMock).toHaveBeenCalledWith('u9');
    });
    await waitFor(() => {
      expect(listMock).toHaveBeenCalledTimes(2);
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('Cognito 削除確認ダイアログのキャンセルで removeCognitoUser は呼ばれない', async () => {
    const user = userEvent.setup();
    const removeCognitoMock = vi.fn((): Promise<void> => Promise.resolve());
    const client = makeClient({
      list: vi.fn(
        (): Promise<EmployeeSummary[]> =>
          Promise.resolve([
            {
              employeeId: 'u9',
              name: '退職一郎',
              phoneNumber: '',
              isAdmin: true,
              deleted: true,
            },
          ]),
      ),
      removeCognitoUser: removeCognitoMock,
    });
    renderPage(client);

    await user.click(await screen.findByRole('button', { name: 'Cognito 削除' }));
    await user.click(screen.getByRole('button', { name: 'キャンセル' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(removeCognitoMock).not.toHaveBeenCalled();
  });

  it('Cognito 削除で API エラーならサーバーメッセージを表示する', async () => {
    const user = userEvent.setup();
    const removeCognitoMock = vi.fn(
      (): Promise<void> =>
        Promise.reject(
          new EmployeeApiError(
            409,
            'Employee must be logically deleted before Cognito user deletion',
          ),
        ),
    );
    const client = makeClient({
      list: vi.fn(
        (): Promise<EmployeeSummary[]> =>
          Promise.resolve([
            {
              employeeId: 'u9',
              name: '退職一郎',
              phoneNumber: '',
              isAdmin: true,
              deleted: true,
            },
          ]),
      ),
      removeCognitoUser: removeCognitoMock,
    });
    renderPage(client);

    await user.click(await screen.findByRole('button', { name: 'Cognito 削除' }));
    await user.click(screen.getByRole('button', { name: 'Cognito 削除する' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Employee must be logically deleted before Cognito user deletion',
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Cognito 削除に失敗しました');
    expect(screen.getByRole('alert')).toHaveTextContent('HTTP 409');
  });
});
