/**
 * TranscriptViewerPage の振る舞いテスト（Phase 10.7、Requirement 12.2）。
 *
 * 観点：
 *   - RecordingApi で署名付き URL を取得し、S3 URL から JSON を取得 → text を表示。
 *   - 410 Gone のとき「保管期間（90 日）を超過」メッセージ。
 *   - 認証エラー / S3 5xx などはエラーバナー表示。
 *   - parseTranscript 単体：Transcribe 形式 JSON のテキストと信頼度を抽出する。
 */

import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { RecordingApiError, type RecordingClient } from '../api/recordingClient';

import { parseTranscript } from './transcriptParser';
import { TranscriptViewerPage } from './TranscriptViewerPage';

function makeRecordingClient(
  impl: () => Promise<{ url: string; expiresInSeconds: number; bucket: string; key: string }>,
): { client: RecordingClient; getCycleTranscript: ReturnType<typeof vi.fn> } {
  const fn = vi.fn(impl);
  return {
    client: { getCycleTranscript: fn } as unknown as RecordingClient,
    getCycleTranscript: fn,
  };
}

describe('parseTranscript (pure)', () => {
  it('Transcribe 形式から text を抽出し、items から信頼度の平均を計算', () => {
    const raw = JSON.stringify({
      results: {
        transcripts: [{ transcript: '無事です' }],
        items: [
          { alternatives: [{ confidence: '0.95', content: '無事' }], type: 'pronunciation' },
          { alternatives: [{ confidence: '0.85', content: 'です' }], type: 'pronunciation' },
        ],
      },
    });
    const out = parseTranscript(raw);
    expect(out.text).toBe('無事です');
    expect(out.confidence).not.toBeNull();
    if (out.confidence === null) throw new Error('unreachable');
    expect(out.confidence).toBeCloseTo(0.9, 4);
  });

  it('items 無しでも text のみ返す（confidence=null）', () => {
    const raw = JSON.stringify({ results: { transcripts: [{ transcript: 'hello' }] } });
    const out = parseTranscript(raw);
    expect(out.text).toBe('hello');
    expect(out.confidence).toBeNull();
  });

  it('JSON ではない文字列はエラー', () => {
    expect(() => parseTranscript('not json')).toThrow();
  });

  it('results.transcripts が空配列のときエラー', () => {
    const raw = JSON.stringify({ results: { transcripts: [] } });
    expect(() => parseTranscript(raw)).toThrow();
  });
});

describe('TranscriptViewerPage', () => {
  it('署名付き URL を取得後、S3 から本文を取得して表示する', async () => {
    const recording = makeRecordingClient(() =>
      Promise.resolve({
        url: 'https://example.s3/transcript.json',
        expiresInSeconds: 900,
        bucket: 'tr',
        key: 'cycles/cyc-1/emp-A#1.json',
      }),
    );
    const httpFetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          results: { transcripts: [{ transcript: '無事です。被害はありません。' }] },
        }),
        { status: 200 },
      ),
    );

    render(
      <MemoryRouter>
        <TranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          cycleId="cyc-1"
          employeeId="emp-A"
          seq="1"
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('transcript-text')).toBeInTheDocument();
    });
    expect(screen.getByTestId('transcript-text')).toHaveTextContent('無事です。被害はありません。');
    expect(recording.getCycleTranscript).toHaveBeenCalledWith('cyc-1', 'emp-A', '1');
    expect(httpFetch).toHaveBeenCalledWith('https://example.s3/transcript.json');
  });

  it('410 Gone のとき「保管期間超過」メッセージを表示する', async () => {
    const recording = makeRecordingClient(() =>
      Promise.reject(new RecordingApiError(410, 'expired', '2025-01-01T00:00:00Z')),
    );
    const httpFetch = vi.fn();
    render(
      <MemoryRouter>
        <TranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          cycleId="cyc-old"
          employeeId="emp-A"
          seq="1"
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('transcript-gone')).toHaveTextContent('保管期間（90 日）を超過');
    });
    expect(httpFetch).not.toHaveBeenCalled();
  });

  it('S3 5xx のときエラーバナーを表示する', async () => {
    const recording = makeRecordingClient(() =>
      Promise.resolve({
        url: 'https://example.s3/transcript.json',
        expiresInSeconds: 900,
        bucket: 'tr',
        key: 'k',
      }),
    );
    const httpFetch = vi
      .fn()
      .mockResolvedValue(new Response('Internal Server Error', { status: 500 }));

    render(
      <MemoryRouter>
        <TranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          cycleId="cyc-1"
          employeeId="emp-A"
          seq="1"
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('transcript-error')).toHaveTextContent('HTTP 500');
    });
  });

  it('パラメータが空のときエラーを表示し RecordingClient を呼ばない', async () => {
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );
    const httpFetch = vi.fn();
    render(
      <MemoryRouter>
        <TranscriptViewerPage
          recordingClient={recording.client}
          httpFetch={httpFetch}
          cycleId=""
          employeeId=""
          seq=""
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('transcript-error')).toHaveTextContent('未指定');
    });
    expect(recording.getCycleTranscript).not.toHaveBeenCalled();
    expect(httpFetch).not.toHaveBeenCalled();
  });
});
