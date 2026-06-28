/**
 * InboundClient の単体テスト（Phase 10.8、Requirement 13.7）。
 *
 * 観点：
 *   - GET /inbound が正しい URL で叩かれる（nextToken なし）。
 *   - 200 応答で `{ items, pageSize, nextToken }` がそのまま返る。
 *   - nextToken が query パラメータとして URL エンコードされて付与される。
 *   - nextToken=null は最終ページとして null のまま返る。
 *   - 500 / shape 不正は `InboundApiError` として透過する。
 */

import { describe, expect, it, vi } from 'vitest';

import { InboundApiError, InboundClient } from './inboundClient';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const SAMPLE_ROW = {
  contactId: 'c-1',
  receivedAt: '2026-06-25T01:23:45Z',
  callerNumberMasked: '+*******1234',
  cycleId: 'cyc-1',
  employeeId: 'emp-A',
  employeeName: '社員 A',
  flow: 'ACTIVE_CYCLE',
  voiceStatus: 'SAFE',
  transcriptExcerpt: '無事です',
};

describe('InboundClient.list', () => {
  it('GET /inbound を呼び出し、items / pageSize / nextToken を返す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [SAMPLE_ROW],
        pageSize: 50,
        nextToken: 'tok-2',
      }),
    );
    const client = new InboundClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.list();

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/inbound', { method: 'GET' });
    expect(result.items).toHaveLength(1);
    expect(result.items[0]?.contactId).toBe('c-1');
    expect(result.items[0]?.flow).toBe('ACTIVE_CYCLE');
    expect(result.items[0]?.callerNumberMasked).toBe('+*******1234');
    expect(result.pageSize).toBe(50);
    expect(result.nextToken).toBe('tok-2');
  });

  it('nextToken を渡すと query パラメータとして付与する', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [],
        pageSize: 50,
        nextToken: null,
      }),
    );
    const client = new InboundClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.list('offset 50/abc');

    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/inbound?nextToken=offset%2050%2Fabc',
      { method: 'GET' },
    );
  });

  it('nextToken=null は最終ページとして null のまま返る', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [SAMPLE_ROW],
        pageSize: 50,
        nextToken: null,
      }),
    );
    const client = new InboundClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.list();
    expect(result.nextToken).toBeNull();
  });

  it('500 は InboundApiError として透過する', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(500, { error: 'Internal server error: scan failed' }));
    const client = new InboundClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client.list().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(InboundApiError);
    if (!(err instanceof InboundApiError)) throw err;
    expect(err.status).toBe(500);
    expect(err.serverMessage).toBe('Internal server error: scan failed');
  });

  it('shape 不正（items が配列でない）は InboundApiError(shape)', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: 'not array',
        pageSize: 0,
        nextToken: null,
      }),
    );
    const client = new InboundClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.list()).rejects.toBeInstanceOf(InboundApiError);
  });

  it('flow が不正値の行は shape エラーで弾く', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [{ ...SAMPLE_ROW, flow: 'UNKNOWN_FLOW' }],
        pageSize: 50,
        nextToken: null,
      }),
    );
    const client = new InboundClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.list()).rejects.toBeInstanceOf(InboundApiError);
  });

  it('cycleId / employeeId / 等のオプショナル項目は null へ正規化される', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        items: [
          {
            contactId: 'c-2',
            receivedAt: '2026-06-25T00:00:00Z',
            callerNumberMasked: '+*******9999',
            flow: 'NOT_REGISTERED',
          },
        ],
        pageSize: 50,
        nextToken: null,
      }),
    );
    const client = new InboundClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.list();
    expect(result.items[0]).toMatchObject({
      contactId: 'c-2',
      cycleId: null,
      employeeId: null,
      employeeName: null,
      flow: 'NOT_REGISTERED',
      voiceStatus: null,
      transcriptExcerpt: null,
    });
  });
});
