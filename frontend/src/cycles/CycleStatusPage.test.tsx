/**
 * CycleStatusPage の振る舞いテスト（Phase 10.6）。
 *
 * 観点：
 *   - 初回マウントで getStatus が呼ばれ、応答内容（summary / items / degraded）が画面に出る。
 *   - 指定ポーリング間隔で setInterval が動作し、複数回呼出が発生する。
 *   - getStatus 失敗時はエラーバナー + 直前値保持 + 次周期で再取得継続。
 *   - status=COMPLETED の応答到達でポーリングが停止する。
 *   - status=TIMEOUT の応答到達でもポーリングが停止する。
 *   - 縮退情報（degraded[]）はすべての component 名が警告バナーに表示される。
 *   - degraded=[] のとき警告バナーは出ない（Property 25 の対偶）。
 *
 * Note：real timer を使い、ポーリング間隔は 50ms 程度の短い値を指定する（合計テスト時間 < 数秒）。
 * fake timer は setInterval 登録時点で `runOnlyPendingTimersAsync` が初回 tick を発火させて
 * しまい呼出回数の境界条件が曖昧になるため不採用。
 */

import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { type CycleClient, type CycleStatusSnapshot } from '../api/cycleClient';

import { CycleStatusPage } from './CycleStatusPage';

function makeSnapshot(overrides: Partial<CycleStatusSnapshot> = {}): CycleStatusSnapshot {
  const base: CycleStatusSnapshot = {
    cycleId: 'cyc-test',
    status: 'RUNNING',
    summary: {
      targetTotal: 3,
      dispatched: 2,
      responded: 1,
      unreachable: 0,
      byStatus: { SAFE: 1, INJURED: 0, UNAVAILABLE: 0, OTHER: 0, UNREACHABLE: 0, PENDING: 2 },
    },
    items: [
      {
        employeeId: 'emp-A',
        name: '社員 A',
        currentStatus: 'SAFE',
        callAttempts: 1,
        lastResponseAt: '2026-06-25T01:05:00Z',
        transcriptExcerpt: '無事です',
      },
      {
        employeeId: 'emp-B',
        name: '社員 B',
        currentStatus: 'PENDING',
        callAttempts: 1,
        lastResponseAt: null,
        transcriptExcerpt: '',
      },
    ],
    degraded: [],
  };
  return { ...base, ...overrides };
}

function makeClient(
  impl: (cycleId: string, signal?: AbortSignal) => Promise<CycleStatusSnapshot>,
): { client: CycleClient; getStatus: ReturnType<typeof vi.fn> } {
  const getStatus = vi.fn(impl);
  const fake = { getStatus };
  return { client: fake as unknown as CycleClient, getStatus };
}

const SHORT_INTERVAL_MS = 80;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('CycleStatusPage', () => {
  it('初回マウントで getStatus が呼ばれ、summary と items を画面に表示する', async () => {
    const snap = makeSnapshot();
    const { client, getStatus } = makeClient(() => Promise.resolve(snap));

    render(
      <CycleStatusPage client={client} cycleId="cyc-test" pollingIntervalMs={SHORT_INTERVAL_MS} />,
    );

    await waitFor(() => {
      expect(getStatus).toHaveBeenCalled();
    });
    expect(getStatus).toHaveBeenCalledWith('cyc-test', expect.any(AbortSignal));

    await waitFor(() => {
      expect(screen.getByTestId('summary-target-total')).toHaveTextContent('3');
    });
    expect(screen.getByTestId('summary-dispatched')).toHaveTextContent('2');
    expect(screen.getByTestId('summary-responded')).toHaveTextContent('1');
    expect(screen.getByTestId('summary-unreachable')).toHaveTextContent('0');
    expect(screen.getByTestId('status-count-SAFE')).toHaveTextContent('無事: 1');
    expect(screen.getByTestId('status-count-PENDING')).toHaveTextContent('未応答: 2');
    expect(screen.getByTestId('status-top')).toHaveTextContent('実行中');
    expect(screen.getByTestId('item-status-emp-A')).toHaveTextContent('無事');
    expect(screen.getByTestId('item-status-emp-B')).toHaveTextContent('未応答');
    expect(screen.getByTestId('item-excerpt-emp-A')).toHaveTextContent('無事です');
    // 未応答の lastResponseAt は「未応答」と表示。
    expect(screen.getByTestId('status-item-emp-B')).toHaveTextContent('未応答');
    // 終端到達前なので polling 停止表示は無い。
    expect(screen.queryByTestId('status-polling-stopped')).toBeNull();
    // degraded=[] なら警告バナーは出ない。
    expect(screen.queryByTestId('status-degraded-banner')).toBeNull();
  });

  it('指定ポーリング間隔で setInterval により getStatus が複数回呼ばれる', async () => {
    const { client, getStatus } = makeClient(() => Promise.resolve(makeSnapshot()));
    render(
      <CycleStatusPage client={client} cycleId="cyc-test" pollingIntervalMs={SHORT_INTERVAL_MS} />,
    );

    await waitFor(() => {
      expect(getStatus).toHaveBeenCalledTimes(1);
    });

    // SHORT_INTERVAL_MS x 3 ぶん待って 3 回以上呼出されることを確認（タイミングずれ吸収のため >=）。
    await sleep(SHORT_INTERVAL_MS * 3 + 30);
    expect(getStatus.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it('status=COMPLETED の応答到達でポーリングが停止する（以降 setInterval が呼出を発行しない）', async () => {
    const { client, getStatus } = makeClient(() =>
      Promise.resolve(makeSnapshot({ status: 'COMPLETED' })),
    );
    render(
      <CycleStatusPage client={client} cycleId="cyc-test" pollingIntervalMs={SHORT_INTERVAL_MS} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('status-polling-stopped')).toBeInTheDocument();
    });
    expect(getStatus.mock.calls.length).toBe(1);

    // 以後 interval を超えて待っても呼出は増えない。
    await sleep(SHORT_INTERVAL_MS * 3 + 30);
    expect(getStatus.mock.calls.length).toBe(1);
    expect(screen.getByTestId('status-top')).toHaveTextContent('完了');
  });

  it('status=TIMEOUT の応答到達でもポーリングが停止する', async () => {
    const { client, getStatus } = makeClient(() =>
      Promise.resolve(makeSnapshot({ status: 'TIMEOUT' })),
    );
    render(
      <CycleStatusPage client={client} cycleId="cyc-test" pollingIntervalMs={SHORT_INTERVAL_MS} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('status-top')).toHaveTextContent('タイムアウト');
    });
    expect(screen.getByTestId('status-polling-stopped')).toBeInTheDocument();

    const callsAfterStop = getStatus.mock.calls.length;
    await sleep(SHORT_INTERVAL_MS * 3 + 30);
    expect(getStatus.mock.calls.length).toBe(callsAfterStop);
  });

  it('getStatus 失敗時はエラーバナー + 直前値保持 + 次周期で再取得継続', async () => {
    const okSnap = makeSnapshot({
      cycleId: 'cyc-prev',
      summary: {
        targetTotal: 5,
        dispatched: 5,
        responded: 3,
        unreachable: 0,
        byStatus: { SAFE: 3, INJURED: 0, UNAVAILABLE: 0, OTHER: 0, UNREACHABLE: 0, PENDING: 2 },
      },
    });
    // 呼出シーケンス：ok, fail, ok, ok, ok ...（fail 後は ok を返し続ける）。
    let count = 0;
    const { client } = makeClient(() => {
      const seq = count;
      count += 1;
      if (seq === 1) return Promise.reject(new Error('network down'));
      return Promise.resolve(okSnap);
    });

    render(
      <CycleStatusPage client={client} cycleId="cyc-prev" pollingIntervalMs={SHORT_INTERVAL_MS} />,
    );

    // 初回成功で targetTotal=5 が表示される。
    await waitFor(() => {
      expect(screen.getByTestId('summary-target-total')).toHaveTextContent('5');
    });

    // 2 回目で失敗 → エラーバナー表示。直前値は保持。
    await waitFor(() => {
      expect(screen.getByTestId('status-error-banner')).toBeInTheDocument();
    });
    expect(screen.getByTestId('summary-target-total')).toHaveTextContent('5');

    // 3 回目以降で成功復帰 → エラーバナー消える。
    await waitFor(() => {
      expect(screen.queryByTestId('status-error-banner')).toBeNull();
    });
    expect(screen.getByTestId('summary-target-total')).toHaveTextContent('5');
  });

  it('縮退情報があると警告バナーにすべての component 名が表示される', async () => {
    const snap = makeSnapshot({
      degraded: [
        { component: 'Amazon Transcribe', since: '2026-06-25T01:00:00Z' },
        { component: 'Amazon Connect', since: '2026-06-25T01:05:00Z' },
      ],
    });
    const { client } = makeClient(() => Promise.resolve(snap));
    render(
      <CycleStatusPage client={client} cycleId="cyc-test" pollingIntervalMs={SHORT_INTERVAL_MS} />,
    );

    const banner = await screen.findByTestId('status-degraded-banner');
    expect(banner).toHaveTextContent('Amazon Transcribe');
    expect(banner).toHaveTextContent('Amazon Connect');
  });

  it('cycleId が指定されていないとき警告メッセージを表示し getStatus を呼ばない', async () => {
    const { client, getStatus } = makeClient(() => Promise.resolve(makeSnapshot()));
    render(<CycleStatusPage client={client} cycleId="" pollingIntervalMs={SHORT_INTERVAL_MS} />);

    // 何もしなくても警告は同期的に出る。
    expect(getStatus).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('確認IDが指定されていません');

    // 念のため少し待っても呼ばれない。
    await sleep(SHORT_INTERVAL_MS + 20);
    expect(getStatus).not.toHaveBeenCalled();
  });
});
