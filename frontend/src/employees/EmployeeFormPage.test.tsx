/**
 * EmployeeFormPage の振る舞いテスト。
 *
 * 観点：
 *   - 新規追加：氏名 + 国内形式番号で create 呼出 → /employees にナビゲート。
 *   - 編集：マウント時に get → 初期値を国内形式で埋めて update。
 *   - 国内形式違反 / 氏名空：API は呼ばれず、フィールド別エラー表示。
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
  it('氏名と国内形式番号で create を呼び、一覧へナビゲートする', async () => {
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
    await user.type(screen.getByLabelText(/電話番号/), '09012345678');
    await user.click(screen.getByRole('button', { name: '追加する' }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({
        name: '山田太郎',
        phoneNumber: '+819012345678',
      });
    });
    expect(await screen.findByTestId('list')).toBeInTheDocument();
  });

  it('国内形式違反は create を呼ばずフィールドエラーを表示する', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn();
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/氏名/), '山田');
    await user.type(screen.getByLabelText(/電話番号/), '0901234'); // 桁数不足
    await user.click(screen.getByRole('button', { name: '追加する' }));

    // role=alert がフィールド直下に表示される。
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((el) => el.textContent?.includes('国内形式') ?? false)).toBe(true);
    expect(createMock).not.toHaveBeenCalled();
  });

  it('氏名が空白のみのとき create を呼ばずフィールドエラーを表示する', async () => {
    const user = userEvent.setup();
    const createMock = vi.fn();
    const client = makeClient({ create: createMock });
    renderNewMode(client);

    await user.type(screen.getByLabelText(/電話番号/), '09000000001');
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
    await user.type(screen.getByLabelText(/電話番号/), '09000000001');
    await user.click(screen.getByRole('button', { name: '追加する' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Phone number already registered');
  });
});

describe('EmployeeFormPage (編集モード)', () => {
  it('マウント時に get を呼び、初期値を国内形式で埋めたうえで update する', async () => {
    const user = userEvent.setup();
    const getMock = vi.fn(
      (): Promise<EmployeeDetail> =>
        Promise.resolve({
          employeeId: 'u1',
          name: '田中花子',
          phoneNumber: '+819011110000',
          isAdmin: false,
          createdAt: '2026-06-25T00:00:00Z',
        }),
    );
    const updateMock = vi.fn(
      (): Promise<EmployeeSummary> =>
        Promise.resolve({
          employeeId: 'u1',
          name: '田中花',
          phoneNumber: '+819022220000',
          isAdmin: false,
        }),
    );
    const client = makeClient({ get: getMock, update: updateMock });
    renderEditMode(client);

    expect(await screen.findByDisplayValue('田中花子')).toBeInTheDocument();
    // E.164 の +819011110000 が国内形式 09011110000 で表示される
    expect(screen.getByDisplayValue('09011110000')).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith('u1');

    const nameInput = screen.getByLabelText(/氏名/);
    await user.clear(nameInput);
    await user.type(nameInput, '田中花');

    const phoneInput = screen.getByLabelText(/電話番号/);
    await user.clear(phoneInput);
    await user.type(phoneInput, '09022220000');

    await user.click(screen.getByRole('button', { name: '更新する' }));

    await waitFor(() => {
      expect(updateMock).toHaveBeenCalledWith('u1', {
        name: '田中花',
        phoneNumber: '+819022220000',
      });
    });
  });
});
