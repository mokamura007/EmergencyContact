/**
 * DictionaryClient の単体テスト（Phase 10.9、Requirement 8.1〜8.4 / 8.7）。
 *
 * 観点：
 *   - list() が GET /keyword-dictionary と GET /keyword-dictionary/version を
 *     並列で呼び出し、`{ categories, version }` に合成して返す。
 *   - getVersion() が GET /keyword-dictionary/version を呼び出し version を返す。
 *   - add() / remove() / touch() がそれぞれ POST / DELETE / PATCH を
 *     正しい URL + body で叩き、新 version を返す（200/201 双方を許容）。
 *   - 409 Conflict は `DictionaryConflictError`（latestVersion=null）として透過。
 *   - 500 は `DictionaryApiError` として透過（serverMessage 保持）。
 *   - shape 不正は `DictionaryApiError` として throw。
 *   - 未知カテゴリは無視され、3 カテゴリ全部空配列でも正常返却。
 */

import { describe, expect, it, vi } from 'vitest';

import { DictionaryApiError, DictionaryClient, DictionaryConflictError } from './dictionaryClient';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('DictionaryClient.list', () => {
  it('GET /keyword-dictionary と GET /keyword-dictionary/version を並列発火し、categories + version を合成する', async () => {
    const authFetch = vi.fn((url: string | URL) => {
      if (String(url).endsWith('/keyword-dictionary')) {
        return Promise.resolve(
          jsonResponse(200, {
            SAFE: ['無事', '元気'],
            INJURED: ['怪我'],
            UNAVAILABLE: [],
          }),
        );
      }
      if (String(url).endsWith('/keyword-dictionary/version')) {
        return Promise.resolve(jsonResponse(200, { version: 42 }));
      }
      throw new Error(`Unexpected URL ${String(url)}`);
    });
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const snapshot = await client.list();

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/keyword-dictionary', {
      method: 'GET',
    });
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/keyword-dictionary/version', {
      method: 'GET',
    });
    expect(snapshot.version).toBe(42);
    expect(snapshot.categories.SAFE).toEqual(['無事', '元気']);
    expect(snapshot.categories.INJURED).toEqual(['怪我']);
    expect(snapshot.categories.UNAVAILABLE).toEqual([]);
  });

  it('全カテゴリ空でも 3 カテゴリ全部空配列として返す', async () => {
    const authFetch = vi.fn((url: string | URL) => {
      if (String(url).endsWith('/keyword-dictionary')) {
        return Promise.resolve(jsonResponse(200, { SAFE: [], INJURED: [], UNAVAILABLE: [] }));
      }
      return Promise.resolve(jsonResponse(200, { version: 0 }));
    });
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const snapshot = await client.list();
    expect(snapshot.version).toBe(0);
    expect(snapshot.categories.SAFE).toEqual([]);
    expect(snapshot.categories.INJURED).toEqual([]);
    expect(snapshot.categories.UNAVAILABLE).toEqual([]);
  });

  it('カテゴリのキーが配列でない場合 shape エラーで throw', async () => {
    const authFetch = vi.fn((url: string | URL) => {
      if (String(url).endsWith('/keyword-dictionary')) {
        return Promise.resolve(
          jsonResponse(200, { SAFE: 'not array', INJURED: [], UNAVAILABLE: [] }),
        );
      }
      return Promise.resolve(jsonResponse(200, { version: 1 }));
    });
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.list()).rejects.toBeInstanceOf(DictionaryApiError);
  });

  it('未知カテゴリ（FOO 等）は無視され 3 カテゴリは正常に読める', async () => {
    const authFetch = vi.fn((url: string | URL) => {
      if (String(url).endsWith('/keyword-dictionary')) {
        return Promise.resolve(
          jsonResponse(200, {
            SAFE: ['ok'],
            INJURED: [],
            UNAVAILABLE: [],
            FOO: ['ignored'],
          }),
        );
      }
      return Promise.resolve(jsonResponse(200, { version: 3 }));
    });
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const snapshot = await client.list();
    expect(snapshot.categories.SAFE).toEqual(['ok']);
    expect(snapshot.version).toBe(3);
  });

  it('500 エラー時は DictionaryApiError として透過する', async () => {
    const authFetch = vi.fn((url: string | URL) => {
      if (String(url).endsWith('/keyword-dictionary')) {
        return Promise.resolve(jsonResponse(500, { error: 'DDB scan failed' }));
      }
      return Promise.resolve(jsonResponse(200, { version: 1 }));
    });
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client.list().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DictionaryApiError);
    if (!(err instanceof DictionaryApiError)) throw err;
    expect(err.status).toBe(500);
    expect(err.serverMessage).toBe('DDB scan failed');
  });
});

describe('DictionaryClient.getVersion', () => {
  it('GET /keyword-dictionary/version を叩き、version 番号を返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(jsonResponse(200, { version: 7 }));
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.getVersion();
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/keyword-dictionary/version', {
      method: 'GET',
    });
    expect(result.version).toBe(7);
  });
});

describe('DictionaryClient.add', () => {
  it('POST /keyword-dictionary に category / keyword / expectedVersion を送る', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(201, { category: 'SAFE', keyword: '無事', version: 8 }));
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.add('SAFE', '無事', 7);

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/keyword-dictionary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: 'SAFE', keyword: '無事', expectedVersion: 7 }),
    });
    expect(result).toEqual({ category: 'SAFE', keyword: '無事', version: 8 });
  });

  it('409 Conflict 時は DictionaryConflictError として throw（latestVersion=null）', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(409, {
        error: 'Concurrent modification detected; refresh the dictionary version and retry',
      }),
    );
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client.add('SAFE', '元気', 1).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DictionaryConflictError);
    if (!(err instanceof DictionaryConflictError)) throw err;
    expect(err.status).toBe(409);
    expect(err.latestVersion).toBeNull();
    expect(err.serverMessage).toContain('Concurrent modification');
  });
});

describe('DictionaryClient.remove', () => {
  it('DELETE /keyword-dictionary/{category}/{keyword} に expectedVersion を送る', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { category: 'INJURED', keyword: '怪我', version: 9 }));
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.remove('INJURED', '怪我', 8);

    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/keyword-dictionary/INJURED/%E6%80%AA%E6%88%91',
      {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expectedVersion: 8 }),
      },
    );
    expect(result.version).toBe(9);
  });
});

describe('DictionaryClient.touch', () => {
  it('PATCH /keyword-dictionary/{category}/{keyword} で新 version を取得する', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { category: 'SAFE', keyword: '無事', version: 11 }));
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.touch('SAFE', '無事', 10);

    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/keyword-dictionary/SAFE/%E7%84%A1%E4%BA%8B',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expectedVersion: 10 }),
      },
    );
    expect(result.version).toBe(11);
  });
});

describe('DictionaryClient mutation shape validation', () => {
  it('mutation レスポンスの category が不正値なら DictionaryApiError(shape)', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(201, { category: 'UNKNOWN', keyword: 'x', version: 1 }));
    const client = new DictionaryClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.add('SAFE', 'x', 0)).rejects.toBeInstanceOf(DictionaryApiError);
  });
});
