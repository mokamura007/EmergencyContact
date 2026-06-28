/**
 * Status_Viewer 純粋ロジックのユニットテスト（Phase 10.6）。
 *
 * Property 18 / 25 の核となる reducer / renderDegraded を網羅する。
 * 13.18 / 13.25 PBT は本ファイルとは別に Phase 13 で追加される。
 */

import { describe, expect, it } from 'vitest';

import {
  initialStatusViewerState,
  isTerminalStatus,
  renderDegraded,
  statusViewerReducer,
  type CycleStatusSnapshot,
  type StatusViewerState,
} from './statusReducer';

function makeSnapshot(overrides: Partial<CycleStatusSnapshot> = {}): CycleStatusSnapshot {
  const base: CycleStatusSnapshot = {
    cycleId: 'cyc-test',
    status: 'RUNNING',
    summary: {
      targetTotal: 1,
      dispatched: 0,
      responded: 0,
      unreachable: 0,
      byStatus: { SAFE: 0, INJURED: 0, UNAVAILABLE: 0, OTHER: 0, UNREACHABLE: 0, PENDING: 1 },
    },
    items: [],
    degraded: [],
  };
  return { ...base, ...overrides };
}

describe('statusViewerReducer (Property 18 受け皿)', () => {
  it('初期状態は lastSuccess=null / errorFlag=false / pollingStopped=false', () => {
    expect(initialStatusViewerState).toEqual({
      lastSuccess: null,
      errorFlag: false,
      pollingStopped: false,
    });
  });

  it('SUCCESS で lastSuccess を更新し errorFlag を false にする', () => {
    const snap = makeSnapshot({ status: 'RUNNING' });
    const next = statusViewerReducer(
      { lastSuccess: null, errorFlag: true, pollingStopped: false },
      { type: 'SUCCESS', snapshot: snap },
    );
    expect(next.lastSuccess).toBe(snap);
    expect(next.errorFlag).toBe(false);
    expect(next.pollingStopped).toBe(false);
  });

  it('SUCCESS かつ status=COMPLETED で pollingStopped=true になる', () => {
    const snap = makeSnapshot({ status: 'COMPLETED' });
    const next = statusViewerReducer(initialStatusViewerState, {
      type: 'SUCCESS',
      snapshot: snap,
    });
    expect(next.pollingStopped).toBe(true);
  });

  it('SUCCESS かつ status=TIMEOUT で pollingStopped=true になる', () => {
    const snap = makeSnapshot({ status: 'TIMEOUT' });
    const next = statusViewerReducer(initialStatusViewerState, {
      type: 'SUCCESS',
      snapshot: snap,
    });
    expect(next.pollingStopped).toBe(true);
  });

  it('SUCCESS かつ status=START_FAILED で pollingStopped=true になる（再試行不可）', () => {
    const snap = makeSnapshot({ status: 'START_FAILED' });
    const next = statusViewerReducer(initialStatusViewerState, {
      type: 'SUCCESS',
      snapshot: snap,
    });
    expect(next.pollingStopped).toBe(true);
  });

  it('FAILURE のとき lastSuccess は不変、errorFlag=true、pollingStopped はそのまま', () => {
    const prev = makeSnapshot({ cycleId: 'cyc-prev' });
    const state: StatusViewerState = {
      lastSuccess: prev,
      errorFlag: false,
      pollingStopped: false,
    };
    const next = statusViewerReducer(state, { type: 'FAILURE' });
    expect(next.lastSuccess).toBe(prev); // 直前値保持（Requirement 11.6）
    expect(next.errorFlag).toBe(true);
    expect(next.pollingStopped).toBe(false);
  });

  it('FAILURE → SUCCESS の遷移で errorFlag が false に戻る', () => {
    const state: StatusViewerState = {
      lastSuccess: makeSnapshot(),
      errorFlag: true,
      pollingStopped: false,
    };
    const snap = makeSnapshot({ cycleId: 'cyc-new' });
    const next = statusViewerReducer(state, { type: 'SUCCESS', snapshot: snap });
    expect(next.errorFlag).toBe(false);
    expect(next.lastSuccess).toBe(snap);
  });

  it('pollingStopped=true の状態で FAILURE が来ても pollingStopped は true のまま', () => {
    const state: StatusViewerState = {
      lastSuccess: makeSnapshot({ status: 'COMPLETED' }),
      errorFlag: false,
      pollingStopped: true,
    };
    const next = statusViewerReducer(state, { type: 'FAILURE' });
    expect(next.pollingStopped).toBe(true);
    expect(next.errorFlag).toBe(true);
  });
});

describe('isTerminalStatus', () => {
  it.each(['COMPLETED', 'TIMEOUT', 'START_FAILED'] as const)('%s は終端', (s) => {
    expect(isTerminalStatus(s)).toBe(true);
  });
  it('RUNNING は終端ではない', () => {
    expect(isTerminalStatus('RUNNING')).toBe(false);
  });
});

describe('renderDegraded (Property 25 受け皿)', () => {
  it('空配列を渡すと空配列を返す（出力にコンポーネント名は含まれない）', () => {
    expect(renderDegraded([])).toEqual([]);
  });

  it('1 件の縮退情報からその component 名を返す', () => {
    expect(
      renderDegraded([{ component: 'Amazon Transcribe', since: '2026-06-25T01:00:00Z' }]),
    ).toEqual(['Amazon Transcribe']);
  });

  it('複数件の縮退情報からすべての component 名を順序保持で返す', () => {
    const out = renderDegraded([
      { component: 'Amazon Transcribe', since: '2026-06-25T01:00:00Z' },
      { component: 'Amazon Connect', since: '2026-06-25T01:05:00Z' },
      { component: 'Amazon DynamoDB', since: '2026-06-25T01:10:00Z' },
    ]);
    expect(out).toEqual(['Amazon Transcribe', 'Amazon Connect', 'Amazon DynamoDB']);
  });
});
