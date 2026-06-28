/**
 * InboundListPage の振る舞いテスト（Phase 10.8、Requirement 13.7）。
 *
 * 観点：
 *   - マウント時に inboundClient.list() が呼ばれ、受信時刻降順で表示される。
 *   - サーバー nextToken でページ送りが動作（前ページにも戻れる）。
 *   - 空表示（着信履歴なし）。
 *   - API エラー時の serverMessage 表示。
 *   - 90 日以内 + ACTIVE_CYCLE の行：再生ボタン押下で getInboundRecording →
 *     <audio> インライン展開、Transcript リンクが /inbound/:contactId/transcript へ。
 *   - 90 日超過の行：再生ボタン disabled + 「保管期限切れ」表示。
 *   - flow≠ACTIVE_CYCLE の行（NOT_REGISTERED 等）：disabled + 「録音なし」表示。
 *   - 410 Gone：押下時に「保管期間を超過したため再生できません」表示。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import {
  InboundApiError,
  type InboundClient,
  type InboundContactRow,
  type InboundContactsPage,
} from '../api/inboundClient';
import { RecordingApiError, type RecordingClient } from '../api/recordingClient';

import { InboundListPage } from './InboundListPage';

function makeRow(overrides: Partial<InboundContactRow> = {}): InboundContactRow {
  return {
    contactId: 'c-1',
    receivedAt: '2026-06-25T01:00:00Z',
    callerNumberMasked: '+*******1234',
    cycleId: 'cyc-1',
    employeeId: 'emp-A',
    employeeName: '社員 A',
    flow: 'ACTIVE_CYCLE',
    voiceStatus: 'SAFE',
    transcriptExcerpt: '無事です',
    ...overrides,
  };
}

function makePage(
  rows: readonly InboundContactRow[],
  nextToken: string | null = null,
): InboundContactsPage {
  return { items: rows, pageSize: 50, nextToken };
}

function makeInboundClient(pageImpl: (token?: string) => Promise<InboundContactsPage>): {
  client: InboundClient;
  list: ReturnType<typeof vi.fn>;
} {
  const list = vi.fn(pageImpl);
  return {
    client: { list } as unknown as InboundClient,
    list,
  };
}

function makeRecordingClient(
  getInboundRecording: (contactId: string) => Promise<{
    url: string;
    expiresInSeconds: number;
    bucket: string;
    key: string;
  }>,
): { client: RecordingClient; getInboundRecording: ReturnType<typeof vi.fn> } {
  const fn = vi.fn(getInboundRecording);
  return {
    client: { getInboundRecording: fn } as unknown as RecordingClient,
    getInboundRecording: fn,
  };
}

describe('InboundListPage', () => {
  it('マウント時に list() が呼ばれ、各行が表示される', async () => {
    const rows = [
      makeRow({ contactId: 'c-1' }),
      makeRow({ contactId: 'c-2', employeeName: '社員 B' }),
    ];
    const { client, list } = makeInboundClient(() => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <InboundListPage
          inboundClient={client}
          recordingClient={recording.client}
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-row-c-1')).toBeInTheDocument();
    });
    expect(screen.getByTestId('inbound-row-c-2')).toBeInTheDocument();
    expect(screen.getByTestId('inbound-caller-c-1')).toHaveTextContent('+*******1234');
    expect(screen.getByTestId('inbound-flow-c-1')).toHaveTextContent('受付済');
    expect(list).toHaveBeenCalledWith(undefined);
  });

  it('録音再生ボタン押下で getInboundRecording → <audio> 表示', async () => {
    const rows = [makeRow({ contactId: 'c-1' })];
    const { client } = makeInboundClient(() => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.resolve({
        url: 'https://example.s3/inbound.wav',
        expiresInSeconds: 900,
        bucket: 'rec',
        key: 'inbound/c-1.wav',
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <InboundListPage
          inboundClient={client}
          recordingClient={recording.client}
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    const playButton = await screen.findByTestId('inbound-play-button-c-1');
    expect(playButton).toBeEnabled();
    await user.click(playButton);

    await waitFor(() => {
      expect(screen.getByTestId('inbound-audio-c-1')).toBeInTheDocument();
    });
    expect(recording.getInboundRecording).toHaveBeenCalledWith('c-1');
    const audio = screen.getByTestId('inbound-audio-c-1');
    expect(audio).toHaveAttribute('src', 'https://example.s3/inbound.wav');
    expect(screen.getByTestId('inbound-transcript-link-c-1')).toHaveAttribute(
      'href',
      '/inbound/c-1/transcript',
    );
  });

  it('90 日超過の行は disabled + 「保管期間（90 日）超過」表示', async () => {
    const rows = [makeRow({ contactId: 'c-old', receivedAt: '2026-01-01T00:00:00Z' })];
    const { client } = makeInboundClient(() => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <InboundListPage
          inboundClient={client}
          recordingClient={recording.client}
          // receivedAt から 100 日後（90 日超過）
          nowFactory={() => new Date('2026-04-15T00:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-play-button-c-old')).toBeDisabled();
    });
    expect(screen.getByTestId('inbound-transcript-disabled-c-old')).toBeInTheDocument();
    expect(screen.getByTestId('inbound-disabled-reason-c-old')).toHaveTextContent(
      '保管期間（90 日）超過',
    );
  });

  it('flow≠ACTIVE_CYCLE の行は disabled + 「録音なし」表示', async () => {
    const rows = [
      makeRow({
        contactId: 'c-nr',
        flow: 'NOT_REGISTERED',
        cycleId: null,
        employeeId: null,
        employeeName: null,
        voiceStatus: null,
        transcriptExcerpt: null,
      }),
    ];
    const { client } = makeInboundClient(() => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <InboundListPage
          inboundClient={client}
          recordingClient={recording.client}
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-play-button-c-nr')).toBeDisabled();
    });
    expect(screen.getByTestId('inbound-disabled-reason-c-nr')).toHaveTextContent('録音なし');
    expect(screen.getByTestId('inbound-disabled-reason-c-nr')).toHaveTextContent('NOT_REGISTERED');
  });

  it('サーバーが 410 Gone を返したら「保管期間を超過」メッセージ表示', async () => {
    const rows = [makeRow({ contactId: 'c-410' })];
    const { client } = makeInboundClient(() => Promise.resolve(makePage(rows)));
    const recording = makeRecordingClient(() =>
      Promise.reject(new RecordingApiError(410, 'expired', '2025-01-01T00:00:00Z')),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <InboundListPage
          inboundClient={client}
          recordingClient={recording.client}
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    const playButton = await screen.findByTestId('inbound-play-button-c-410');
    await user.click(playButton);
    await waitFor(() => {
      expect(screen.getByTestId('inbound-recording-error-c-410')).toHaveTextContent(
        '保管期間（90 日）を超過',
      );
    });
  });

  it('next/prev ページ送りが動作する', async () => {
    const page1 = makePage([makeRow({ contactId: 'c-1' })], 'tok-2');
    const page2 = makePage([makeRow({ contactId: 'c-2' })], null);
    const { client, list } = makeInboundClient((token) =>
      Promise.resolve(token === 'tok-2' ? page2 : page1),
    );
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <InboundListPage
          inboundClient={client}
          recordingClient={recording.client}
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-row-c-1')).toBeInTheDocument();
    });

    await user.click(screen.getByTestId('inbound-next-page'));
    await waitFor(() => {
      expect(screen.getByTestId('inbound-row-c-2')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('inbound-row-c-1')).toBeNull();
    expect(list).toHaveBeenCalledWith('tok-2');

    await user.click(screen.getByTestId('inbound-prev-page'));
    await waitFor(() => {
      expect(screen.getByTestId('inbound-row-c-1')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('inbound-row-c-2')).toBeNull();
  });

  it('items が空のとき「着信履歴はまだありません」を表示', async () => {
    const { client } = makeInboundClient(() => Promise.resolve(makePage([])));
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <InboundListPage
          inboundClient={client}
          recordingClient={recording.client}
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-empty')).toBeInTheDocument();
    });
  });

  it('API エラー時に serverMessage を表示', async () => {
    const { client } = makeInboundClient(() =>
      Promise.reject(new InboundApiError(500, 'scan failed')),
    );
    const recording = makeRecordingClient(() =>
      Promise.resolve({ url: 'x', expiresInSeconds: 900, bucket: 'b', key: 'k' }),
    );

    render(
      <MemoryRouter>
        <InboundListPage
          inboundClient={client}
          recordingClient={recording.client}
          nowFactory={() => new Date('2026-06-26T01:00:00Z')}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('inbound-error')).toHaveTextContent('HTTP 500');
    });
    expect(screen.getByTestId('inbound-error')).toHaveTextContent('scan failed');
  });
});
