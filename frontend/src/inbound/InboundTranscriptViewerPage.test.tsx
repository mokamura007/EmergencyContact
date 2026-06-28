/**
 * InboundTranscriptViewerPage の振る舞いテスト（Phase 10.8、Requirement 13.7）。
 *
 * 観点：
 *   - 署名付き URL を取得し、S3 から JSON を取得 → 本文を表示。
 *   - 410 Gone のとき「保管期間（90 日）を超過」メッセージ。
 *   - S3 5xx のときエラーバナー表示。
 *   - JSON parse 失敗時はエラーバナー表示。
 */

import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { RecordingApiError, type RecordingClient } from '../api/recordingClient';

import { InboundTranscriptViewerPage } from './InboundTranscriptViewerPage';

function makeRecordingClient(
  impl: () => Promise<{ url: string; expiresInSeconds: number; bucket: string; key: string }>,
): { client: RecordingClient; getInboundTranscript: ReturnType<typeof vi.fn> } {
  const fn = vi.fn(impl);
  return {
    client: { getInboundTranscript: fn } as unknown as RecordingClient,
    getInboundTranscript: fn,
  };
}

describe('InboundTranscriptViewerPage', () => {
  it('署名付き URL を取得後、S3 から本文を取得して表示する', async () => {
    const recording = makeRecordingClient(() =>
      Promise.resolve({
        url: 'https://example.s3/inbound-transcript.json',
        expiresInSeconds: 900,
        bucket: 'tr',
        key: 'inbound/c-1.json',
      }),
    );
    const httpFetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          results: {
            transcripts: [{ transcript: '折り返しました、無事です。' }],
            items: [
              {
                alternatives: [{ confidence: '0.93', content: '折り返し' }],
                type: 'pronunciation',
              },
            ],
          },
        }),
        { status: 200 },
      ),
    );

    render(
      <MemoryRouter>
        <InboundTranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          contactId="c-1"
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-transcript-text')).toBeInTheDocument();
    });
    expect(screen.getByTestId('inbound-transcript-text')).toHaveTextContent(
      '折り返しました、無事です。',
    );
    expect(recording.getInboundTranscript).toHaveBeenCalledWith('c-1');
    expect(httpFetch).toHaveBeenCalledWith('https://example.s3/inbound-transcript.json');
  });

  it('410 Gone のとき「保管期間超過」メッセージを表示する', async () => {
    const recording = makeRecordingClient(() =>
      Promise.reject(new RecordingApiError(410, 'expired', '2025-01-01T00:00:00Z')),
    );
    const httpFetch = vi.fn();
    render(
      <MemoryRouter>
        <InboundTranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          contactId="c-old"
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-transcript-gone')).toHaveTextContent(
        '保管期間（90 日）を超過',
      );
    });
    expect(httpFetch).not.toHaveBeenCalled();
  });

  it('S3 5xx のときエラーバナーを表示する', async () => {
    const recording = makeRecordingClient(() =>
      Promise.resolve({
        url: 'https://example.s3/x.json',
        expiresInSeconds: 900,
        bucket: 'tr',
        key: 'inbound/c-1.json',
      }),
    );
    const httpFetch = vi
      .fn()
      .mockResolvedValue(new Response('Internal Server Error', { status: 500 }));

    render(
      <MemoryRouter>
        <InboundTranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          contactId="c-1"
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-transcript-error')).toHaveTextContent('HTTP 500');
    });
  });

  it('JSON ではないボディの場合エラーバナーを表示', async () => {
    const recording = makeRecordingClient(() =>
      Promise.resolve({
        url: 'https://example.s3/x.json',
        expiresInSeconds: 900,
        bucket: 'tr',
        key: 'inbound/c-1.json',
      }),
    );
    const httpFetch = vi.fn().mockResolvedValue(new Response('not json', { status: 200 }));

    render(
      <MemoryRouter>
        <InboundTranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          contactId="c-1"
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-transcript-error')).toBeInTheDocument();
    });
  });

  it('contactId 空のときエラー表示で RecordingClient を呼ばない', async () => {
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );
    const httpFetch = vi.fn();
    render(
      <MemoryRouter>
        <InboundTranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          contactId=""
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-transcript-error')).toHaveTextContent('未指定');
    });
    expect(recording.getInboundTranscript).not.toHaveBeenCalled();
    expect(httpFetch).not.toHaveBeenCalled();
  });
});
