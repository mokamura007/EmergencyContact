/**
 * CycleDetailPage の振る舞いテスト（Phase 10.7、Requirement 12.1〜12.3）。
 *
 * 観点：
 *   - マウント時に getDetail + listResponses が呼ばれる。
 *   - 各 Response 行に「録音再生」ボタンと Transcript リンクが出る。
 *   - 90 日以内：押下で recordingClient.getCycleRecording が呼ばれ、`<audio>` が表示される。
 *   - 90 日超過：起動時刻ベースで再生ボタン / Transcript リンクが無効化され、
 *                「保管期間（90 日）超過」メッセージが表示される。
 *   - retryCount=0 の行は「録音なし」で無効化される。
 *   - 410 Gone は「保管期間（90 日）を超過したため再生できません」メッセージで表示。
 *   - listResponses の nextToken でページ送りが動作する（前ページに戻れる）。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type {
  CycleClient,
  CycleDetail,
  CycleResponseRow,
  CycleResponsesPage,
} from '../api/cycleClient';
import { RecordingApiError, type RecordingClient } from '../api/recordingClient';

import { CycleDetailPage } from './CycleDetailPage';

function makeDetail(overrides: Partial<CycleDetail> = {}): CycleDetail {
  return {
    cycleId: 'cyc-1',
    status: 'COMPLETED',
    mode: 'ALL',
    startedAt: '2026-06-25T01:00:00Z',
    completedAt: '2026-06-25T01:45:00Z',
    dictionaryVersion: 7,
    retryCount: 3,
    retryIntervalMinutes: 5,
    targetCount: 2,
    referencedCycleId: null,
    extra: {},
    ...overrides,
  };
}

function makeRow(overrides: Partial<CycleResponseRow> = {}): CycleResponseRow {
  return {
    cycleId: 'cyc-1',
    employeeId: 'emp-A',
    employeeName: '社員 A',
    voiceStatus: 'SAFE',
    callResultCode: 'RECORDED',
    retryCount: 1,
    lastCalledAt: '2026-06-25T01:05:00Z',
    finalizedAt: '2026-06-25T01:06:00Z',
    transcriptExcerpt: '無事です',
    ...overrides,
  };
}

function makePage(
  rows: readonly CycleResponseRow[],
  nextToken: string | null = null,
): CycleResponsesPage {
  return { items: rows, pageSize: 50, nextToken };
}

function makeCycleClient(
  detail: CycleDetail,
  pageImpl: (cycleId: string, token?: string) => Promise<CycleResponsesPage>,
): {
  client: CycleClient;
  getDetail: ReturnType<typeof vi.fn>;
  listResponses: ReturnType<typeof vi.fn>;
} {
  const getDetail = vi.fn(() => Promise.resolve(detail));
  const listResponses = vi.fn(pageImpl);
  return {
    client: { getDetail, listResponses } as unknown as CycleClient,
    getDetail,
    listResponses,
  };
}

function makeRecordingClient(
  getCycleRecording: (
    cycleId: string,
    employeeId: string,
    seq: string,
  ) => Promise<{
    url: string;
    expiresInSeconds: number;
    bucket: string;
    key: string;
  }>,
): { client: RecordingClient; getCycleRecording: ReturnType<typeof vi.fn> } {
  const fn = vi.fn(getCycleRecording);
  return {
    client: { getCycleRecording: fn } as unknown as RecordingClient,
    getCycleRecording: fn,
  };
}

describe('CycleDetailPage', () => {
  it('Cycle 情報と Response 一覧を表示する', async () => {
    const detail = makeDetail();
    const rows = [makeRow(), makeRow({ employeeId: 'emp-B', employeeName: '社員 B' })];
    const { client } = makeCycleClient(detail, () => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <CycleDetailPage
          cycleClient={client}
          recordingClient={recording.client}
          cycleId="cyc-1"
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('detail-cycle-id')).toHaveTextContent('cyc-1');
    });
    expect(screen.getByTestId('detail-status')).toHaveTextContent('完了');
    expect(screen.getByTestId('detail-dictionary-version')).toHaveTextContent('7');
    expect(screen.getByTestId('response-row-emp-A')).toBeInTheDocument();
    expect(screen.getByTestId('response-row-emp-B')).toBeInTheDocument();
    expect(screen.getByTestId('response-excerpt-emp-A')).toHaveTextContent('無事です');
    // 90 日超過バナーは出ない。
    expect(screen.queryByTestId('detail-retention-expired-banner')).toBeNull();
  });

  it('録音再生ボタン押下で getCycleRecording を呼び、<audio> を表示する', async () => {
    const detail = makeDetail();
    const rows = [makeRow({ retryCount: 2 })];
    const { client } = makeCycleClient(detail, () => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.resolve({
        url: 'https://example.s3/abc',
        expiresInSeconds: 900,
        bucket: 'rec',
        key: 'cycles/cyc-1/emp-A#2.wav',
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <CycleDetailPage
          cycleClient={client}
          recordingClient={recording.client}
          cycleId="cyc-1"
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    const playButton = await screen.findByTestId('play-button-emp-A');
    expect(playButton).toBeEnabled();
    await user.click(playButton);

    await waitFor(() => {
      expect(screen.getByTestId('audio-emp-A')).toBeInTheDocument();
    });
    expect(recording.getCycleRecording).toHaveBeenCalledWith('cyc-1', 'emp-A', '2');
    const audio = screen.getByTestId('audio-emp-A');
    expect(audio).toHaveAttribute('src', 'https://example.s3/abc');
  });

  it('90 日超過のサイクルでは再生ボタンと Transcript リンクが無効化される', async () => {
    const detail = makeDetail({ startedAt: '2026-01-01T00:00:00Z' });
    const rows = [makeRow({ retryCount: 1 })];
    const { client } = makeCycleClient(detail, () => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <CycleDetailPage
          cycleClient={client}
          recordingClient={recording.client}
          cycleId="cyc-1"
          // startedAt から 100 日後（90 日超過）
          nowFactory={() => new Date('2026-04-15T00:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('detail-retention-expired-banner')).toBeInTheDocument();
    });
    expect(screen.getByTestId('play-button-emp-A')).toBeDisabled();
    expect(screen.getByTestId('transcript-disabled-emp-A')).toBeInTheDocument();
    expect(screen.getByTestId('recording-disabled-reason-emp-A')).toHaveTextContent(
      '保管期間（90 日）超過',
    );
  });

  it('retryCount=0（未架電）の行は「録音なし」で無効化される', async () => {
    const detail = makeDetail();
    const rows = [makeRow({ employeeId: 'emp-Z', retryCount: 0, voiceStatus: 'PENDING' })];
    const { client } = makeCycleClient(detail, () => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <CycleDetailPage
          cycleClient={client}
          recordingClient={recording.client}
          cycleId="cyc-1"
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('play-button-emp-Z')).toBeDisabled();
    });
    expect(screen.getByTestId('recording-disabled-reason-emp-Z')).toHaveTextContent('録音なし');
  });

  it('サーバーが 410 Gone を返したら「保管期間超過」メッセージを表示', async () => {
    const detail = makeDetail();
    const rows = [makeRow({ retryCount: 1 })];
    const { client } = makeCycleClient(detail, () => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.reject(new RecordingApiError(410, 'expired', '2025-01-01T00:00:00Z')),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <CycleDetailPage
          cycleClient={client}
          recordingClient={recording.client}
          cycleId="cyc-1"
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    const playButton = await screen.findByTestId('play-button-emp-A');
    await user.click(playButton);
    await waitFor(() => {
      expect(screen.getByTestId('recording-error-emp-A')).toHaveTextContent(
        '保管期間（90 日）を超過',
      );
    });
  });

  it('Response 一覧の次ページ/前ページ送りが動作する', async () => {
    const detail = makeDetail();
    const page1 = makePage([makeRow({ employeeId: 'emp-1' })], 'tok-2');
    const page2 = makePage([makeRow({ employeeId: 'emp-2' })], null);
    const { client, listResponses } = makeCycleClient(detail, (_cycleId, token) =>
      Promise.resolve(token === 'tok-2' ? page2 : page1),
    );
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <CycleDetailPage
          cycleClient={client}
          recordingClient={recording.client}
          cycleId="cyc-1"
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('response-row-emp-1')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('responses-next-page'));
    await waitFor(() => {
      expect(screen.getByTestId('response-row-emp-2')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('response-row-emp-1')).toBeNull();
    expect(listResponses).toHaveBeenCalledWith('cyc-1', 'tok-2');

    await user.click(screen.getByTestId('responses-prev-page'));
    await waitFor(() => {
      expect(screen.getByTestId('response-row-emp-1')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('response-row-emp-2')).toBeNull();
  });

  it('cycleId が空のとき警告を表示し API を呼ばない', () => {
    const detail = makeDetail();
    const { client, getDetail, listResponses } = makeCycleClient(detail, () =>
      Promise.resolve(makePage([])),
    );
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <CycleDetailPage cycleClient={client} recordingClient={recording.client} cycleId="" />
      </MemoryRouter>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('確認IDが指定されていません');
    expect(getDetail).not.toHaveBeenCalled();
    expect(listResponses).not.toHaveBeenCalled();
  });
});
