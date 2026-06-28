/**
 * RecordingClient の単体テスト（Phase 10.7、Requirement 12.2 / 12.3）。
 *
 * 観点：
 *   - GET /cycles/{id}/recordings/{employeeId}/{seq} が正しい URL で叩かれる。
 *   - 200 応答で `{ url, expiresInSeconds, bucket, key }` がそのまま返る。
 *   - 410 Gone は `RecordingApiError.isGone() === true` で識別できる。
 *   - 404 / 5xx も `RecordingApiError` として透過する。
 *   - Transcript ルートにも同じ契約が適用される。
 *   - パスパラメータは URL エンコードされる（Cycle ID / Employee ID / seq）。
 */

import { describe, expect, it, vi } from 'vitest';

import { RecordingApiError, RecordingClient } from './recordingClient';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('RecordingClient.getCycleRecording', () => {
  it('GET /cycles/{id}/recordings/{employeeId}/{seq} を呼び出す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        url: 'https://example.s3/abc',
        expiresInSeconds: 900,
        bucket: 'rec-bucket',
        key: 'cycles/cyc-1/emp-A#1.wav',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.getCycleRecording('cyc-1', 'emp-A', '1');

    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/cycles/cyc-1/recordings/emp-A/1',
      { method: 'GET' },
    );
    expect(result).toEqual({
      url: 'https://example.s3/abc',
      expiresInSeconds: 900,
      bucket: 'rec-bucket',
      key: 'cycles/cyc-1/emp-A#1.wav',
    });
  });

  it('410 Gone は RecordingApiError.isGone()=true で startedAt 付き', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(410, {
        error: 'Recording / transcript has been deleted by 90-day lifecycle policy',
        cycleId: 'cyc-old',
        startedAt: '2025-01-01T00:00:00Z',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client.getCycleRecording('cyc-old', 'emp-A', '1').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RecordingApiError);
    if (!(err instanceof RecordingApiError)) throw err;
    expect(err.status).toBe(410);
    expect(err.isGone()).toBe(true);
    expect(err.referenceTimestamp).toBe('2025-01-01T00:00:00Z');
  });

  it('404 Not Found は RecordingApiError.isGone()=false', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(404, { error: 'Cycle not found: cyc-missing' }));
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client
      .getCycleRecording('cyc-missing', 'emp-A', '1')
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RecordingApiError);
    if (!(err instanceof RecordingApiError)) throw err;
    expect(err.status).toBe(404);
    expect(err.isGone()).toBe(false);
    expect(err.serverMessage).toBe('Cycle not found: cyc-missing');
  });

  it('パスパラメータは URL エンコードされる', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        url: 'https://example.s3/abc',
        expiresInSeconds: 900,
        bucket: 'b',
        key: 'k',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.getCycleRecording('cyc 1', 'emp/A', '1');
    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/cycles/cyc%201/recordings/emp%2FA/1',
      { method: 'GET' },
    );
  });

  it('shape 不正（url なし）は RecordingApiError(shape)', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        expiresInSeconds: 900,
        bucket: 'b',
        key: 'k',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await expect(client.getCycleRecording('cyc-1', 'emp-A', '1')).rejects.toBeInstanceOf(
      RecordingApiError,
    );
  });
});

describe('RecordingClient.getCycleTranscript', () => {
  it('GET /cycles/{id}/transcripts/{employeeId}/{seq} を呼び出す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        url: 'https://example.s3/transcript',
        expiresInSeconds: 900,
        bucket: 'tr-bucket',
        key: 'cycles/cyc-1/emp-A#1.json',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.getCycleTranscript('cyc-1', 'emp-A', '1');

    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/cycles/cyc-1/transcripts/emp-A/1',
      { method: 'GET' },
    );
    expect(result.url).toBe('https://example.s3/transcript');
    expect(result.expiresInSeconds).toBe(900);
  });

  it('410 Gone は isGone()=true', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(410, {
        error: 'expired',
        cycleId: 'cyc-old',
        startedAt: '2024-01-01T00:00:00Z',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client.getCycleTranscript('cyc-old', 'emp-A', '1').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RecordingApiError);
    if (!(err instanceof RecordingApiError)) throw err;
    expect(err.isGone()).toBe(true);
  });
});

describe('RecordingClient.getInboundRecording', () => {
  it('GET /inbound/{contactId}/recording を呼び出す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        url: 'https://example.s3/inbound-rec',
        expiresInSeconds: 900,
        bucket: 'rec-bucket',
        key: 'inbound/c-1.wav',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.getInboundRecording('c-1');

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/inbound/c-1/recording', {
      method: 'GET',
    });
    expect(result.key).toBe('inbound/c-1.wav');
  });

  it('410 Gone は receivedAt 付きの RecordingApiError', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(410, {
        error: 'expired',
        contactId: 'c-old',
        receivedAt: '2025-01-01T00:00:00Z',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client.getInboundRecording('c-old').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RecordingApiError);
    if (!(err instanceof RecordingApiError)) throw err;
    expect(err.isGone()).toBe(true);
    expect(err.referenceTimestamp).toBe('2025-01-01T00:00:00Z');
  });

  it('404 は isGone=false', async () => {
    const authFetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(404, { error: 'Inbound contact not found: c-missing' }));
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const err = await client.getInboundRecording('c-missing').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RecordingApiError);
    if (!(err instanceof RecordingApiError)) throw err;
    expect(err.status).toBe(404);
    expect(err.isGone()).toBe(false);
  });

  it('contactId は URL エンコードされる', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        url: 'x',
        expiresInSeconds: 900,
        bucket: 'b',
        key: 'k',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    await client.getInboundRecording('c 1/abc');
    expect(authFetch).toHaveBeenCalledWith(
      'https://api.example/dev/inbound/c%201%2Fabc/recording',
      { method: 'GET' },
    );
  });
});

describe('RecordingClient.getInboundTranscript', () => {
  it('GET /inbound/{contactId}/transcript を呼び出す', async () => {
    const authFetch = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        url: 'https://example.s3/inbound-transcript',
        expiresInSeconds: 900,
        bucket: 'tr-bucket',
        key: 'inbound/c-1.json',
      }),
    );
    const client = new RecordingClient({ authFetch, baseUrl: 'https://api.example/dev' });
    const result = await client.getInboundTranscript('c-1');

    expect(authFetch).toHaveBeenCalledWith('https://api.example/dev/inbound/c-1/transcript', {
      method: 'GET',
    });
    expect(result.key).toBe('inbound/c-1.json');
  });
});
