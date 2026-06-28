/**
 * EmployeeClient の単体テスト。
 *
 * 観点：
 *   - 認証付き fetch（AuthFetch DI）にリクエストが渡る。
 *   - HTTP 4xx/5xx は EmployeeApiError として throw され、サーバーメッセージを保持する。
 *   - JSON レスポンスのパースとモデル化（list/get/create/update/remove/importCsv）。
 *   - 不正な JSON 形（employees が配列でない等）でも EmployeeApiError で fail-fast。
 */

import { describe, expect, it, vi } from 'vitest';

import { EmployeeApiError, EmployeeClient } from './employeeClient';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('EmployeeClient.list', () => {
  it('GET /employees を呼び、employees 配列をモデル化して返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        employees: [
          { employeeId: 'u1', name: 'A', phoneNumber: '+81', isAdmin: false },
          { employeeId: 'u2', name: 'B', phoneNumber: '+82', isAdmin: true },
        ],
        total: 2,
      }),
    );
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.list();

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/employees', {
      method: 'GET',
    });
    expect(result).toEqual([
      { employeeId: 'u1', name: 'A', phoneNumber: '+81', isAdmin: false },
      { employeeId: 'u2', name: 'B', phoneNumber: '+82', isAdmin: true },
    ]);
  });

  it('includeDeleted=true で ?includeDeleted=true クエリ付き URL を呼ぶ（Task 15.16）', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        employees: [
          {
            employeeId: 'u9',
            name: '退職',
            phoneNumber: '',
            isAdmin: true,
            deleted: true,
          },
        ],
        total: 1,
      }),
    );
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.list({ includeDeleted: true });

    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/employees?includeDeleted=true',
      { method: 'GET' },
    );
    expect(result[0]?.deleted).toBe(true);
  });

  it('HTTP 500 のとき EmployeeApiError を throw する', async () => {
    const authFetch = vi.fn().mockResolvedValue(jsonResponse(500, { error: 'Internal failure' }));
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.list()).rejects.toMatchObject({
      name: 'EmployeeApiError',
      status: 500,
      serverMessage: 'Internal failure',
    });
  });

  it('employees キーが配列でなければ EmployeeApiError', async () => {
    const authFetch = vi.fn().mockResolvedValue(jsonResponse(200, { employees: 'oops' }));
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.list()).rejects.toBeInstanceOf(EmployeeApiError);
  });
});

describe('EmployeeClient.get', () => {
  it('GET /employees/{id} を URL エンコードして呼ぶ', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        employeeId: 'abc/def',
        name: 'C',
        phoneNumber: '+1',
        isAdmin: false,
        createdAt: '2026-06-25T00:00:00Z',
      }),
    );
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const detail = await client.get('abc/def');
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/employees/abc%2Fdef', {
      method: 'GET',
    });
    expect(detail.createdAt).toBe('2026-06-25T00:00:00Z');
  });
});

describe('EmployeeClient.create', () => {
  it('POST /employees に JSON ボディを送る', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        employeeId: 'new-id',
        name: '山田',
        phoneNumber: '+819012345678',
        isAdmin: false,
      }),
    );
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const created = await client.create({ name: '山田', phoneNumber: '+819012345678' });

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/employees', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '山田', phoneNumber: '+819012345678' }),
    });
    expect(created.employeeId).toBe('new-id');
  });

  it('409 重複なら EmployeeApiError(status=409)', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(409, { error: 'Phone number already registered' }));
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.create({ name: '山田', phoneNumber: '+8190' })).rejects.toMatchObject({
      status: 409,
      serverMessage: 'Phone number already registered',
    });
  });
});

describe('EmployeeClient.update / remove', () => {
  it('PUT /employees/{id} に JSON ボディを送る', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        employeeId: 'u1',
        name: 'X',
        phoneNumber: '+1',
        isAdmin: false,
      }),
    );
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.update('u1', { name: 'X', phoneNumber: '+1' });
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/employees/u1', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'X', phoneNumber: '+1' }),
    });
  });

  it('DELETE /employees/{id} の 200 を成功扱いにする', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { employeeId: 'u1', deleted: true }));
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.remove('u1')).resolves.toBeUndefined();
  });

  it('DELETE が 404 ならエラー', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(404, { error: 'Employee not found: u1' }));
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.remove('u1')).rejects.toMatchObject({
      status: 404,
      serverMessage: 'Employee not found: u1',
    });
  });
});

describe('EmployeeClient.removeCognitoUser (Task 15.16)', () => {
  it('DELETE /employees/{id}/cognito-user の 200 を成功扱いにする', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { employeeId: 'u9', cognitoUserDeleted: true }));
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.removeCognitoUser('u9')).resolves.toBeUndefined();
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/employees/u9/cognito-user', {
      method: 'DELETE',
    });
  });

  it('id を URL エンコードする', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { employeeId: 'a/b', cognitoUserDeleted: true }));
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.removeCognitoUser('a/b');
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/employees/a%2Fb/cognito-user', {
      method: 'DELETE',
    });
  });

  it('409 ならサーバーメッセージ付き EmployeeApiError', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(409, {
        error: 'Employee must be logically deleted before Cognito user deletion',
      }),
    );
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.removeCognitoUser('u1')).rejects.toMatchObject({
      status: 409,
      serverMessage: 'Employee must be logically deleted before Cognito user deletion',
    });
  });

  it('403 ならエラー（Administrator 限定 API）', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(403, { error: 'Administrator group required' }));
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.removeCognitoUser('u1')).rejects.toMatchObject({
      status: 403,
      serverMessage: 'Administrator group required',
    });
  });
});

describe('EmployeeClient.importCsv', () => {
  it('成功時に imported/attempted/errors を返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        imported: 2,
        attempted: 2,
        errors: [],
      }),
    );
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.importCsv('aGVsbG8=');
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/employees/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ csvBase64: 'aGVsbG8=' }),
    });
    expect(result).toEqual({ imported: 2, attempted: 2, errors: [] });
  });

  it('400 で行レベル失敗が含まれる場合、EmployeeApiError.importErrors に保持される', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(400, {
        imported: 0,
        attempted: 2,
        errors: [
          { line: 2, reason: 'phoneNumber must be E.164' },
          { line: 3, reason: 'name is required' },
        ],
      }),
    );
    const client = new EmployeeClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client.importCsv('xxx').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(EmployeeApiError);
    if (!(err instanceof EmployeeApiError)) throw err;
    expect(err.status).toBe(400);
    expect(err.importErrors).toEqual([
      { line: 2, reason: 'phoneNumber must be E.164' },
      { line: 3, reason: 'name is required' },
    ]);
  });
});
