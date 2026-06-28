/**
 * CycleClient の単体テスト（Phase 10.5）。
 *
 * 観点：
 *   - POST /cycles に JSON ボディと Idempotency-Key ヘッダが付与される。
 *   - 成功時に cycleId / dictionaryVersion 等の戻り値型が組み立てられる。
 *   - 200 idempotent replay（idempotentReplay=true）も正しく解釈される。
 *   - HTTP 4xx/5xx は CycleApiError として throw され、サーバー側 cycleId
 *     （START_FAILED 等）も伝搬する。
 *   - 不正な JSON 形でも fail-fast（CycleApiError）。
 */

import { describe, expect, it, vi } from 'vitest';

import { CycleApiError, CycleClient } from './cycleClient';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('CycleClient.create', () => {
  it('POST /cycles に JSON ボディと Idempotency-Key ヘッダを送る（mode=ALL）', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        cycleId: 'cyc-1',
        status: 'RUNNING',
        mode: 'ALL',
        startedAt: '2026-06-25T01:00:00Z',
        dictionaryVersion: 7,
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.create(
      { mode: 'ALL', retryCount: 3, retryIntervalMinutes: 5 },
      'idem-uuid-1',
    );

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/cycles', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 'idem-uuid-1',
      },
      body: JSON.stringify({ mode: 'ALL', retryCount: 3, retryIntervalMinutes: 5 }),
    });
    expect(result).toEqual({
      cycleId: 'cyc-1',
      status: 'RUNNING',
      mode: 'ALL',
      startedAt: '2026-06-25T01:00:00Z',
      dictionaryVersion: 7,
    });
  });

  it('mode=UNREACHABLE_ONLY と referencedCycleId をボディに含めて送る', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        cycleId: 'cyc-2',
        status: 'RUNNING',
        mode: 'UNREACHABLE_ONLY',
        startedAt: '2026-06-25T02:00:00Z',
        dictionaryVersion: 8,
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.create(
      {
        mode: 'UNREACHABLE_ONLY',
        retryCount: 3,
        retryIntervalMinutes: 5,
        referencedCycleId: 'prev-cycle',
      },
      'idem-uuid-2',
    );
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/cycles', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': 'idem-uuid-2',
      },
      body: JSON.stringify({
        mode: 'UNREACHABLE_ONLY',
        retryCount: 3,
        retryIntervalMinutes: 5,
        referencedCycleId: 'prev-cycle',
      }),
    });
  });

  it('200 idempotent replay の応答を idempotentReplay=true で返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        cycleId: 'cyc-3',
        status: 'RUNNING',
        startedAt: '2026-06-25T03:00:00Z',
        dictionaryVersion: 9,
        idempotentReplay: true,
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.create(
      { mode: 'ALL', retryCount: 3, retryIntervalMinutes: 5 },
      'idem-uuid-3',
    );
    expect(result.idempotentReplay).toBe(true);
    expect(result.cycleId).toBe('cyc-3');
    expect(result.dictionaryVersion).toBe(9);
  });

  it('400 不正入力は CycleApiError(status=400) として throw する', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(400, { error: 'mode must be one of [ALL, UNREACHABLE_ONLY]' }),
      );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(
      client.create({ mode: 'ALL', retryCount: 3, retryIntervalMinutes: 5 }, 'idem-bad'),
    ).rejects.toMatchObject({
      name: 'CycleApiError',
      status: 400,
      serverMessage: 'mode must be one of [ALL, UNREACHABLE_ONLY]',
    });
  });

  it('409 二重起動は CycleApiError(status=409) として throw する', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(409, { error: 'Another cycle is already RUNNING' }));
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(
      client.create({ mode: 'ALL', retryCount: 3, retryIntervalMinutes: 5 }, 'idem-dup'),
    ).rejects.toMatchObject({
      status: 409,
      serverMessage: 'Another cycle is already RUNNING',
    });
  });

  it('500 SFN 失敗（cycleId 付き）でも cycleId を保持する', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(500, {
        error: 'SFN StartExecution failed',
        cycleId: 'cyc-failed',
        cause: 'access denied',
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client
      .create({ mode: 'ALL', retryCount: 3, retryIntervalMinutes: 5 }, 'idem-500')
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(CycleApiError);
    if (!(err instanceof CycleApiError)) throw err;
    expect(err.status).toBe(500);
    expect(err.cycleId).toBe('cyc-failed');
  });

  it('cycleId が無いレスポンスは CycleApiError(shape) として throw する', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        // 必須フィールド欠落
        status: 'RUNNING',
        startedAt: '2026-06-25T01:00:00Z',
        dictionaryVersion: 1,
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(
      client.create({ mode: 'ALL', retryCount: 3, retryIntervalMinutes: 5 }, 'idem-shape'),
    ).rejects.toBeInstanceOf(CycleApiError);
  });
});

describe('CycleClient.getStatus (Phase 10.6)', () => {
  it('GET /cycles/{id}/status を呼び、summary / items / degraded を組み立てて返す', async () => {
    const responseBody = {
      cycleId: 'cyc-1',
      status: 'RUNNING',
      summary: {
        targetTotal: 2,
        dispatched: 1,
        responded: 0,
        unreachable: 0,
        byStatus: { SAFE: 0, INJURED: 0, UNAVAILABLE: 0, OTHER: 0, UNREACHABLE: 0, PENDING: 2 },
      },
      items: [
        {
          employeeId: 'emp-A',
          name: '社員 A',
          currentStatus: 'PENDING',
          callAttempts: 1,
          lastResponseAt: null,
          transcriptExcerpt: '',
        },
      ],
      degraded: [{ component: 'Amazon Transcribe', since: '2026-06-25T01:00:00Z' }],
    };
    const authFetch = vi.fn().mockResolvedValue(jsonResponse(200, responseBody));
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });

    const result = await client.getStatus('cyc-1');

    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/cycles/cyc-1/status',
      expect.objectContaining({ method: 'GET' }),
    );
    expect(result.cycleId).toBe('cyc-1');
    expect(result.status).toBe('RUNNING');
    expect(result.summary.targetTotal).toBe(2);
    expect(result.summary.byStatus).toEqual({
      SAFE: 0,
      INJURED: 0,
      UNAVAILABLE: 0,
      OTHER: 0,
      UNREACHABLE: 0,
      PENDING: 2,
    });
    expect(result.items).toHaveLength(1);
    expect(result.items[0]).toEqual({
      employeeId: 'emp-A',
      name: '社員 A',
      currentStatus: 'PENDING',
      callAttempts: 1,
      lastResponseAt: null,
      transcriptExcerpt: '',
    });
    expect(result.degraded).toEqual([
      { component: 'Amazon Transcribe', since: '2026-06-25T01:00:00Z' },
    ]);
  });

  it('AbortSignal を fetch の init.signal にそのまま渡す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        cycleId: 'cyc-1',
        status: 'RUNNING',
        summary: {
          targetTotal: 0,
          dispatched: 0,
          responded: 0,
          unreachable: 0,
          byStatus: {},
        },
        items: [],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const controller = new AbortController();
    await client.getStatus('cyc-1', controller.signal);

    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/cycles/cyc-1/status',
      expect.objectContaining({ method: 'GET', signal: controller.signal }),
    );
  });

  it('Cycle ID は URL エンコードされる', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        cycleId: 'cyc-1',
        status: 'RUNNING',
        summary: {
          targetTotal: 0,
          dispatched: 0,
          responded: 0,
          unreachable: 0,
          byStatus: {},
        },
        items: [],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.getStatus('cyc with space/and#hash');
    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/cycles/cyc%20with%20space%2Fand%23hash/status',
      expect.any(Object),
    );
  });

  it('degraded フィールド省略のレスポンスでも空配列で返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        cycleId: 'cyc-1',
        status: 'RUNNING',
        summary: {
          targetTotal: 0,
          dispatched: 0,
          responded: 0,
          unreachable: 0,
          byStatus: {},
        },
        items: [],
        // degraded フィールドなし
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.getStatus('cyc-1');
    expect(result.degraded).toEqual([]);
  });

  it('404 Not Found は CycleApiError(status=404) として throw する', async () => {
    const authFetch = vi.fn().mockResolvedValue(jsonResponse(404, { error: 'Cycle not found' }));
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.getStatus('cyc-missing')).rejects.toMatchObject({
      name: 'CycleApiError',
      status: 404,
      serverMessage: 'Cycle not found',
    });
  });

  it('不正な status 値（未知の上位ステータス）は CycleApiError(shape) として throw する', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        cycleId: 'cyc-1',
        status: 'UNKNOWN_STATE',
        summary: {
          targetTotal: 0,
          dispatched: 0,
          responded: 0,
          unreachable: 0,
          byStatus: {},
        },
        items: [],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.getStatus('cyc-1')).rejects.toBeInstanceOf(CycleApiError);
  });

  it('items の currentStatus が未知の値だと CycleApiError(shape) として throw する', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        cycleId: 'cyc-1',
        status: 'RUNNING',
        summary: {
          targetTotal: 1,
          dispatched: 0,
          responded: 0,
          unreachable: 0,
          byStatus: {},
        },
        items: [
          {
            employeeId: 'emp-A',
            name: '社員 A',
            currentStatus: 'WAT', // 不正値
            callAttempts: 0,
            lastResponseAt: null,
            transcriptExcerpt: '',
          },
        ],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.getStatus('cyc-1')).rejects.toBeInstanceOf(CycleApiError);
  });
});

describe('CycleClient.list (Phase 10.7)', () => {
  it('GET /cycles を呼び、cycles 配列と total を返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        total: 2,
        cycles: [
          {
            cycleId: 'cyc-2',
            status: 'COMPLETED',
            mode: 'ALL',
            startedAt: '2026-06-25T03:00:00Z',
            completedAt: '2026-06-25T03:45:00Z',
            dictionaryVersion: 9,
          },
          {
            cycleId: 'cyc-1',
            status: 'COMPLETED',
            mode: 'ALL',
            startedAt: '2026-06-24T03:00:00Z',
            completedAt: '2026-06-24T03:45:00Z',
            dictionaryVersion: 8,
          },
        ],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.list();

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/cycles', { method: 'GET' });
    expect(result.total).toBe(2);
    expect(result.cycles).toHaveLength(2);
    const first = result.cycles[0];
    if (first === undefined) throw new Error('cycles[0] missing');
    expect(first.cycleId).toBe('cyc-2');
  });

  it('mode が null のレコードを許容する', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        total: 1,
        cycles: [
          {
            cycleId: 'cyc-x',
            status: 'RUNNING',
            startedAt: '2026-06-25T03:00:00Z',
            dictionaryVersion: 1,
            // mode 省略
          },
        ],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.list();
    const row = result.cycles[0];
    if (row === undefined) throw new Error('cycles[0] missing');
    expect(row.mode).toBeNull();
    expect(row.completedAt).toBeNull();
  });

  it('500 Internal Server Error は CycleApiError として throw', async () => {
    const authFetch = vi.fn().mockResolvedValue(jsonResponse(500, { error: 'Internal error' }));
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.list()).rejects.toMatchObject({
      name: 'CycleApiError',
      status: 500,
      serverMessage: 'Internal error',
    });
  });

  it('shape 不正（cycles が配列でない）は CycleApiError', async () => {
    const authFetch = vi.fn().mockResolvedValue(jsonResponse(200, { cycles: 'not array' }));
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.list()).rejects.toBeInstanceOf(CycleApiError);
  });
});

describe('CycleClient.getDetail (Phase 10.7)', () => {
  it('GET /cycles/{id} を呼び、コア項目と extra を返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        cycleId: 'cyc-1',
        status: 'COMPLETED',
        mode: 'ALL',
        startedAt: '2026-06-25T01:00:00Z',
        completedAt: '2026-06-25T01:45:00Z',
        dictionaryVersion: 7,
        retryCount: 3,
        retryIntervalMinutes: 5,
        targetCount: 12,
        idempotencyKey: 'idem-xyz',
        slaWarning30min: false,
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.getDetail('cyc-1');

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/cycles/cyc-1', {
      method: 'GET',
    });
    expect(result.cycleId).toBe('cyc-1');
    expect(result.status).toBe('COMPLETED');
    expect(result.dictionaryVersion).toBe(7);
    expect(result.retryCount).toBe(3);
    expect(result.targetCount).toBe(12);
    expect(result.extra.idempotencyKey).toBe('idem-xyz');
    expect(result.extra.slaWarning30min).toBe(false);
  });

  it('Cycle ID は URL エンコードされる', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        cycleId: 'cyc/1',
        status: 'RUNNING',
        startedAt: '2026-06-25T01:00:00Z',
        dictionaryVersion: 1,
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.getDetail('cyc/1');
    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/cycles/cyc%2F1', {
      method: 'GET',
    });
  });

  it('404 Not Found は CycleApiError(status=404)', async () => {
    const authFetch = vi.fn().mockResolvedValue(jsonResponse(404, { error: 'Cycle not found' }));
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.getDetail('cyc-missing')).rejects.toMatchObject({
      status: 404,
      serverMessage: 'Cycle not found',
    });
  });
});

describe('CycleClient.listResponses (Phase 10.7)', () => {
  it('GET /cycles/{id}/responses を呼び、items / pageSize / nextToken を返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        pageSize: 50,
        nextToken: 'tok-2',
        items: [
          {
            cycleId: 'cyc-1',
            employeeId: 'emp-A',
            employeeName: '社員 A',
            voiceStatus: 'SAFE',
            callResultCode: 'RECORDED',
            retryCount: 1,
            lastCalledAt: '2026-06-25T01:05:00Z',
            finalizedAt: '2026-06-25T01:06:00Z',
            transcriptExcerpt: '無事です',
          },
          {
            cycleId: 'cyc-1',
            employeeId: 'emp-B',
            employeeName: '社員 B',
            voiceStatus: 'PENDING',
            callResultCode: null,
            retryCount: 0,
            lastCalledAt: null,
            finalizedAt: null,
            transcriptExcerpt: null,
          },
        ],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.listResponses('cyc-1');

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/cycles/cyc-1/responses', {
      method: 'GET',
    });
    expect(result.pageSize).toBe(50);
    expect(result.nextToken).toBe('tok-2');
    expect(result.items).toHaveLength(2);
    const [row0, row1] = result.items;
    if (row0 === undefined || row1 === undefined) throw new Error('items missing');
    expect(row0.employeeName).toBe('社員 A');
    expect(row0.voiceStatus).toBe('SAFE');
    expect(row1.voiceStatus).toBe('PENDING');
    expect(row1.callResultCode).toBeNull();
    expect(row1.transcriptExcerpt).toBeNull();
  });

  it('nextToken を渡すと query string として付与する', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        pageSize: 50,
        nextToken: null,
        items: [],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.listResponses('cyc-1', '{"x":"y"}');
    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/cycles/cyc-1/responses?nextToken=%7B%22x%22%3A%22y%22%7D',
      { method: 'GET' },
    );
  });

  it('nextToken=null（最終ページ）を返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        pageSize: 50,
        nextToken: null,
        items: [],
      }),
    );
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.listResponses('cyc-1');
    expect(result.nextToken).toBeNull();
    expect(result.items).toEqual([]);
  });

  it('items が配列でないと CycleApiError', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { items: 'oops', pageSize: 50, nextToken: null }));
    const client = new CycleClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.listResponses('cyc-1')).rejects.toBeInstanceOf(CycleApiError);
  });
});
