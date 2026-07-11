/**
 * EmployeeFormPage の振る舞いテスト。
 *
 * 観点：
 *   - 新規追加：氏名 + E.164 で create 呼出 → /employees にナビゲート。
 *   - 編集：マウント時に get → 初期値を埋めて update。
 *   - E.164 違反 / 氏名空：API は呼ばれず、フィールド別エラー表示。
 *   - サーバー側 409 重複：エラー表示にサーバーメッセージを含む。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { EmployeeClient, EmployeeDetail, EmployeeSummary } from '../api/employeeClient';
import { EmployeeApiError } from '../api/employeeClient';

import { EmployeeFormPage } from './EmployeeFormPage';

function makeClient(overrides: Partial<EmployeeClient>): EmployeeClient {
  const fail = (n: string) => (): never => {
    throw new Error(`unexpected client.${n}`);
  };
  const fake = {
    list: vi.fn(fail('list')),
    get: vi.fn(fail('get')),
    create: vi.fn(fail('create')),
    update: vi.fn(fail('update')),
    remove: vi.fn(fail('remove')),
    importCsv: vi.fn(fail('importCsv')),
    ...overrides,
  };
  return fake as unknown as EmployeeClient;
}

function renderNewMode(client: EmployeeClient): void {
  render(
    <MemoryRouter initialEntries={['/employees/new']}>
      <Routes>
        <Route path="/employees/new" element={<EmployeeFormPage client={client} />} />
        <Route path="/employees" element={<div data-testid="list">list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderEditMode(client: EmployeeClient): void {
  render(
    <MemoryRouter initialEntries={['/employees/u1/edit']}>
      <Routes>
        <Route path="/employees/:id/edit" element={<EmployeeFormPage client={client} />} />
        <Route path="/employees" element={<div data-testid="list">list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('EmployeeFormPage (新規追加)', () => {
  it('氏名と E.164 番号で create を呼び、一覧へナビゲートする', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<EmployeeSummary> =>
        Promise.resolve({
          employeeId: 'new-id',
          name: '山田太郎',
          phoneNumber: '+819012345678',
          isAdmin: false,
        }),
    );
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/氏名/), '山田太郎');
    await user.type(screen.getByLabelText(/電話番号/), '+819012345678');
    await user.click(screen.getByRole('button', { name: '追加する' }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({
        name: '山田太郎',
        phoneNumber: '+819012345678',
      });
    });
    expect(await screen.findByTestId('list')).toBeInTheDocument();
  });

  it('E.164 違反は create を呼ばずフィールドエラーを表示する', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn();
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/氏名/), '山田');
    await user.type(screen.getByLabelText(/電話番号/), '0901234'); // 先頭 + なし
    await user.click(screen.getByRole('button', { name: '追加する' }));

    // role=alert がフィールド直下に表示される。
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((el) => el.textContent?.includes('E.164') ?? false)).toBe(true);
    expect(createMock).not.toHaveBeenCalled();
  });

  it('氏名が空白のみのとき create を呼ばずフィールドエラーを表示する', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn();
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/電話番号/), '+1');
    // 氏名フィールドは空のままで送信。
    await user.click(screen.getByRole('button', { name: '追加する' }));

    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((el) => el.textContent?.includes('氏名') ?? false)).toBe(true);
    expect(createMock).not.toHaveBeenCalled();
  });

  it('サーバー側 409 重複ならサーバーメッセージを表示する', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<EmployeeSummary> =>
        Promise.reject(new EmployeeApiError(409, 'Phone number already registered')),
    );
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/氏名/), '山田');
    await user.type(screen.getByLabelText(/電話番号/), '+8190');
    await user.click(screen.getByRole('button', { name: '追加する' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Phone number already registered');
  });
});

describe('EmployeeFormPage (新規追加 / 管理者権限セクション、Req 2.1 改訂)', () => {
  it('管理者チェック未 ON なら isAdmin / adminEmail を送信しない（既存挙動維持）', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<EmployeeSummary> =>
        Promise.resolve({
          employeeId: 'e1',
          name: '一般',
          phoneNumber: '+819011112222',
          isAdmin: false,
        }),
    );
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    // 管理者セクションは存在するが、チェック OFF の初期状態。
    expect(screen.getByTestId('admin-section')).toBeInTheDocument();
    expect(screen.queryByTestId('admin-email-input')).not.toBeInTheDocument();

    await user.type(screen.getByLabelText(/氏名/), '一般');
    await user.type(screen.getByLabelText(/電話番号/), '+819011112222');
    await user.click(screen.getByRole('button', { name: '追加する' }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledTimes(1);
    });
    // isAdmin / adminEmail は payload に含まれない（未指定）。
    // `mock.calls` の tuple 推論が空タプルになる vitest 型付けを回避するため
    // `unknown[][]` を経由してキャストする（issue #3 対応の副次修正、
    // .kiro/specs/fix-initial-login-flow/tasks.md Task 6 前提）。
    const payload = (createMock.mock.calls as unknown[][])[0]?.[0] as Record<string, unknown>;
    expect(payload).toEqual({ name: '一般', phoneNumber: '+819011112222' });
    expect('isAdmin' in payload).toBe(false);
    expect('adminEmail' in payload).toBe(false);
  });

  it('管理者チェック ON にすると email 欄が表示される', async () => {
    const user = userEvent.setup();
    const client = makeClient({});
    renderNewMode(client);

    expect(screen.queryByTestId('admin-email-input')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('admin-checkbox'));
    expect(screen.getByTestId('admin-email-input')).toBeInTheDocument();
  });

  it('管理者チェック ON + email 空 → create を呼ばずフィールドエラー', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn();
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/氏名/), '管理者太郎');
    await user.type(screen.getByLabelText(/電話番号/), '+819033334444');
    await user.click(screen.getByTestId('admin-checkbox'));
    // adminEmail は入力せずに送信。
    await user.click(screen.getByRole('button', { name: '追加する' }));

    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((el) => el.textContent?.includes('管理者 email') ?? false)).toBe(true);
    expect(createMock).not.toHaveBeenCalled();
  });

  it('管理者チェック ON + email 不正形式 → create を呼ばずフィールドエラー', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn();
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/氏名/), '管理者太郎');
    await user.type(screen.getByLabelText(/電話番号/), '+819033334444');
    await user.click(screen.getByTestId('admin-checkbox'));
    await user.type(screen.getByTestId('admin-email-input'), 'not-an-email');
    await user.click(screen.getByRole('button', { name: '追加する' }));

    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((el) => el.textContent?.includes('管理者 email') ?? false)).toBe(true);
    expect(createMock).not.toHaveBeenCalled();
  });

  it('管理者チェック ON + valid email → isAdmin=true + adminEmail 付きで送信', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn(
      (): Promise<EmployeeSummary> =>
        Promise.resolve({
          employeeId: 'a1',
          name: '管理者太郎',
          phoneNumber: '+819033334444',
          isAdmin: true,
        }),
    );
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/氏名/), '管理者太郎');
    await user.type(screen.getByLabelText(/電話番号/), '+819033334444');
    await user.click(screen.getByTestId('admin-checkbox'));
    await user.type(screen.getByTestId('admin-email-input'), 'admin.taro@example.com');
    await user.click(screen.getByRole('button', { name: '追加する' }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({
        name: '管理者太郎',
        phoneNumber: '+819033334444',
        isAdmin: true,
        adminEmail: 'admin.taro@example.com',
      });
    });
    expect(await screen.findByTestId('list')).toBeInTheDocument();
  });
});

describe('EmployeeFormPage (編集モード：管理者権限欄は非表示、Req 2.1 改訂スコープ外)', () => {
  it('編集モードでは管理者セクションが描画されない', async () => {
    const getMock = vi.fn(
      (): Promise<EmployeeDetail> =>
        Promise.resolve({
          employeeId: 'u1',
          name: '既存太郎',
          phoneNumber: '+8190',
          isAdmin: true,
          createdAt: '2026-06-25T00:00:00Z',
        }),
    );
    const client = makeClient({ get: getMock });
    renderEditMode(client);

    // 読み込み完了を待つ。
    expect(await screen.findByDisplayValue('既存太郎')).toBeInTheDocument();

    // 管理者セクションは描画されない。
    expect(screen.queryByTestId('admin-section')).not.toBeInTheDocument();
    expect(screen.queryByTestId('admin-checkbox')).not.toBeInTheDocument();
    expect(screen.queryByTestId('admin-email-input')).not.toBeInTheDocument();
  });
});

describe('EmployeeFormPage (編集モード)', () => {
  it('マウント時に get を呼び、初期値を埋めたうえで update する', async () => {
    const user = userEvent.setup();
    const getMock = vi.fn(
      (): Promise<EmployeeDetail> =>
        Promise.resolve({
          employeeId: 'u1',
          name: '田中花子',
          phoneNumber: '+8190',
          isAdmin: false,
          createdAt: '2026-06-25T00:00:00Z',
        }),
    );
    const updateMock = vi.fn(
      (): Promise<EmployeeSummary> =>
        Promise.resolve({
          employeeId: 'u1',
          name: '田中花',
          phoneNumber: '+819011110000',
          isAdmin: false,
        }),
    );
    const client = makeClient({ get: getMock, update: updateMock });
    renderEditMode(client);

    expect(await screen.findByDisplayValue('田中花子')).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith('u1');

    const nameInput = screen.getByLabelText(/氏名/);
    await user.clear(nameInput);
    await user.type(nameInput, '田中花');

    const phoneInput = screen.getByLabelText(/電話番号/);
    await user.clear(phoneInput);
    await user.type(phoneInput, '+819011110000');

    await user.click(screen.getByRole('button', { name: '更新する' }));

    await waitFor(() => {
      expect(updateMock).toHaveBeenCalledWith('u1', {
        name: '田中花',
        phoneNumber: '+819011110000',
      });
    });
  });
});
