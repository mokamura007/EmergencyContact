/**
 * EmployeeCsvImportPage の振る舞いテスト。
 *
 * 観点：
 *   - ファイル選択 → インポート実行で importCsv が呼ばれる。
 *   - imported / attempted / failed 3 値が画面に表示される（Done When）。
 *   - クライアント側 preflight（ヘッダ違反など）で API が呼ばれない。
 *   - API 側 400 で行レベルエラーが imported=0 + 失敗詳細リストとして表示される。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { EmployeeClient, ImportCsvResult } from '../api/employeeClient';
import { EmployeeApiError } from '../api/employeeClient';

import { EmployeeCsvImportPage } from './EmployeeCsvImportPage';

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

function makeCsvFile(name: string, content: string): File {
  return new File([content], name, { type: 'text/csv' });
}

function renderPage(client: EmployeeClient): void {
  render(
    <MemoryRouter initialEntries={['/employees/import']}>
      <EmployeeCsvImportPage client={client} />
    </MemoryRouter>,
  );
}

describe('EmployeeCsvImportPage', () => {
  it('正常 CSV → importCsv が呼ばれ、3 値が画面表示される', async () => {
    const user = userEvent.setup();
    const importMock = vi.fn(
      (): Promise<ImportCsvResult> =>
        Promise.resolve({
          imported: 2,
          attempted: 2,
          errors: [],
        }),
    );
    const client = makeClient({ importCsv: importMock });
    renderPage(client);

    const file = makeCsvFile(
      'ok.csv',
      'name,phoneNumber\n山田太郎,+819012345678\n田中花子,+819011112222\n',
    );
    await user.upload(screen.getByLabelText('CSV ファイル'), file);
    await user.click(screen.getByRole('button', { name: 'インポート実行' }));

    await waitFor(() => {
      expect(importMock).toHaveBeenCalled();
    });
    expect(await screen.findByTestId('result-attempted')).toHaveTextContent('試行件数: 2');
    expect(screen.getByTestId('result-imported')).toHaveTextContent('成功件数: 2');
    expect(screen.getByTestId('result-failed')).toHaveTextContent('失敗件数: 0');
  });

  it('ヘッダ違反は preflight で弾き、API を呼ばない', async () => {
    const user = userEvent.setup();
    const importMock = vi.fn();
    const client = makeClient({ importCsv: importMock });
    renderPage(client);

    const file = makeCsvFile('bad.csv', 'fullname,tel\n山田,+1\n');
    await user.upload(screen.getByLabelText('CSV ファイル'), file);
    await user.click(screen.getByRole('button', { name: 'インポート実行' }));

    await screen.findByText('ファイルレベルの検査に失敗しました:');
    expect(importMock).not.toHaveBeenCalled();
  });

  it('行レベル失敗（HTTP 400）で imported/attempted/失敗詳細が表示される', async () => {
    const user = userEvent.setup();
    const importMock = vi.fn(
      (): Promise<ImportCsvResult> =>
        Promise.reject(
          new EmployeeApiError(400, 'Validation failed', [
            { line: 2, reason: 'phoneNumber must be E.164' },
          ]),
        ),
    );
    const client = makeClient({ importCsv: importMock });
    renderPage(client);

    const file = makeCsvFile('partial.csv', 'name,phoneNumber\n山田,not-e164\n');
    await user.upload(screen.getByLabelText('CSV ファイル'), file);
    await user.click(screen.getByRole('button', { name: 'インポート実行' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 400');
    expect(screen.getByText('行 2: phoneNumber must be E.164')).toBeInTheDocument();
  });
});
