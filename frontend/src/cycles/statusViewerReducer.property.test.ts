/**
 * Property 18：ポーリング状態機械（Phase 13.18）。
 *
 * **Validates: Requirements 11.1, 11.5, 11.6**
 *
 * design.md の定義（Property 18）：
 *   _For all_ ステータス取得結果シーケンス `Q = [q_1, q_2, ...]`（各 q_i は
 *   `Success(status)` または `Failure`）について、Status_Viewer の表示値
 *   `display(Q)` は次を満たす：
 *     - `display` は `Q` 中の最後の `Success` の値、初期値は空。
 *     - いずれかの `q_i = Success(status)` で `status ∈ {COMPLETED, TIMEOUT}`
 *       以降、ポーリング呼出は発行されない。
 *     - `q_i = Failure` のとき `display(Q[1..i]) = display(Q[1..i-1])` であり、
 *       エラー表示フラグが立つ。
 *
 * Done When（tasks.md 13.18）：
 *   - COMPLETED/TIMEOUT 以降のポーリング 0。
 *   - Failure 時に直前値保持。
 *
 * 実装対象：`frontend/src/cycles/statusReducer.ts::statusViewerReducer`
 * （Phase 10.6 実装済の純粋関数）。
 *
 * 終端判定の Spec 拡張に関する注意：
 *   design.md の Property 18 は終端を `{COMPLETED, TIMEOUT}` と定義しているが、
 *   実装側 `isTerminalStatus` は `START_FAILED` も終端扱いする（再ポーリングで
 *   回復しない起動時異常を反映した実装拡張）。本 PBT は実装の `isTerminalStatus`
 *   を真値として参照することで、Spec の不等式（実装の終端集合 ⊇ Spec の終端集合）
 *   を Property 上で逸脱させないようにしている。Spec 側の表現拡張については
 *   要求変更プロセスで取り扱う（PBT 側で勝手に判定基準を変えない）。
 *
 * 設計戦略（13.25 renderDegraded.property.test.ts と整合）：
 *   - fast-check で `numRuns: 100`（design.md PBT 設定準拠）。
 *   - 述語は早期 `return false` スタイルで shrinking に委ねる（19原則(b)）。
 *   - arbitrary はローカル const として宣言。snapshot 内の `summary` /
 *     `items` / `degraded` は Property の本質に直接寄与しないため、必要最小限
 *     のサイズで生成して shrinking 効率を確保する。
 *   - 単発遷移性質（P1/P2）と畳み込み性質（P3/P4）を両方検証する
 *     （状態機械 PBT の定石）。P3 は対称性推論（第17原則）に従い順 / 逆双方の
 *     含意を確認する。
 */

import { describe, it } from 'vitest';
import fc from 'fast-check';

import {
  statusViewerReducer,
  isTerminalStatus,
  initialStatusViewerState,
  type CycleStatusSnapshot,
  type CycleTopStatus,
  type StatusViewerEvent,
  type StatusViewerState,
  type VoiceStatusValue,
} from './statusReducer';

// ----------------------------------------------------------------------------
// Arbitraries（PBT 観点で必要最小限の生成）
// ----------------------------------------------------------------------------

const cycleTopStatusArb: fc.Arbitrary<CycleTopStatus> = fc.constantFrom<CycleTopStatus>(
  'RUNNING',
  'COMPLETED',
  'TIMEOUT',
  'START_FAILED',
);

const voiceStatusValueArb: fc.Arbitrary<VoiceStatusValue> = fc.constantFrom<VoiceStatusValue>(
  'SAFE',
  'INJURED',
  'UNAVAILABLE',
  'OTHER',
  'UNREACHABLE',
  'PENDING',
);

const isoDateArb: fc.Arbitrary<string> = fc
  .date({ min: new Date('2020-01-01T00:00:00Z'), max: new Date('2030-12-31T23:59:59Z') })
  .map((d) => d.toISOString());

const snapshotArb: fc.Arbitrary<CycleStatusSnapshot> = fc.record({
  cycleId: fc.string({ minLength: 1, maxLength: 16 }),
  status: cycleTopStatusArb,
  summary: fc.record({
    targetTotal: fc.nat({ max: 1000 }),
    dispatched: fc.nat({ max: 1000 }),
    responded: fc.nat({ max: 1000 }),
    unreachable: fc.nat({ max: 1000 }),
    byStatus: fc.dictionary(fc.string({ minLength: 1, maxLength: 8 }), fc.nat({ max: 1000 })),
  }),
  items: fc.array(
    fc.record({
      employeeId: fc.string({ minLength: 1, maxLength: 16 }),
      name: fc.string({ maxLength: 16 }),
      currentStatus: voiceStatusValueArb,
      callAttempts: fc.nat({ max: 10 }),
      lastResponseAt: fc.option(isoDateArb, { nil: null }),
      transcriptExcerpt: fc.string({ maxLength: 32 }),
    }),
    { maxLength: 3 },
  ),
  degraded: fc.array(
    fc.record({
      component: fc.string({ maxLength: 16 }),
      since: isoDateArb,
    }),
    { maxLength: 2 },
  ),
});

const eventArb: fc.Arbitrary<StatusViewerEvent> = fc.oneof(
  snapshotArb.map<StatusViewerEvent>((snapshot) => ({ type: 'SUCCESS', snapshot })),
  fc.constant<StatusViewerEvent>({ type: 'FAILURE' }),
);

/**
 * Property 18 の仕様前提（design.md「終端到達以降ポーリング呼出は発行されない」、
 * および設計指針 P3「途中で FAILURE が来ても pollingStopped は維持される」）を
 * 反映したイベント列 arbitrary。
 *
 * 制約：終端 SUCCESS（`isTerminalStatus(snapshot.status) === true`）が一度
 *       出現した後は、以降のイベントは FAILURE のみとする。終端後の SUCCESS は
 *       仕様前提として発生しない（上位タイマ層がポーリング自体を停止する）。
 *
 * 生成戦略：任意イベント列を生成した後、最初の終端 SUCCESS のインデックス `k` を
 *           探し、`k` より後の SUCCESS イベントを FAILURE に置換する map で実装
 *           する。これにより fast-check の shrinking 効率を損なわない。
 */
const terminalAwareEventsArb: fc.Arbitrary<readonly StatusViewerEvent[]> = fc
  .array(eventArb, { maxLength: 32 })
  .map((events) => {
    let terminalSeen = false;
    return events.map<StatusViewerEvent>((ev) => {
      if (terminalSeen && ev.type === 'SUCCESS') {
        return { type: 'FAILURE' };
      }
      if (ev.type === 'SUCCESS' && isTerminalStatus(ev.snapshot.status)) {
        terminalSeen = true;
      }
      return ev;
    });
  });

/** 任意の `StatusViewerState`。reducer はこれを受け取れる必要がある。 */
const stateArb: fc.Arbitrary<StatusViewerState> = fc.record({
  lastSuccess: fc.option(snapshotArb, { nil: null }),
  errorFlag: fc.boolean(),
  pollingStopped: fc.boolean(),
});

// ----------------------------------------------------------------------------
// Property 18 本体
// ----------------------------------------------------------------------------

describe('Property 18: ポーリング状態機械 (statusViewerReducer)', () => {
  it('P1: FAILURE 単発遷移は lastSuccess と pollingStopped を保存し errorFlag を立てる', () => {
    fc.assert(
      fc.property(stateArb, (state) => {
        const next = statusViewerReducer(state, { type: 'FAILURE' });
        if (next.lastSuccess !== state.lastSuccess) {
          return false;
        }
        if (!next.errorFlag) {
          return false;
        }
        if (next.pollingStopped !== state.pollingStopped) {
          return false;
        }
        return true;
      }),
      { numRuns: 100 },
    );
  });

  it('P2: SUCCESS 単発遷移は lastSuccess を更新し errorFlag をリセットし pollingStopped を終端判定に同期させる', () => {
    fc.assert(
      fc.property(stateArb, snapshotArb, (state, snapshot) => {
        const next = statusViewerReducer(state, { type: 'SUCCESS', snapshot });
        if (next.lastSuccess !== snapshot) {
          return false;
        }
        if (next.errorFlag) {
          return false;
        }
        if (next.pollingStopped !== isTerminalStatus(snapshot.status)) {
          return false;
        }
        return true;
      }),
      { numRuns: 100 },
    );
  });

  it('P3: 終端 SUCCESS 到達後は最終 pollingStopped=true、未到達ならば false（順 / 逆双方の検証）', () => {
    fc.assert(
      fc.property(terminalAwareEventsArb, (events) => {
        const finalState = events.reduce(statusViewerReducer, initialStatusViewerState);
        const reachedTerminal = events.some(
          (ev) => ev.type === 'SUCCESS' && isTerminalStatus(ev.snapshot.status),
        );
        // 順方向：終端 SUCCESS が一度でもあれば最終 pollingStopped=true。
        // 逆方向：終端 SUCCESS が一度もなければ最終 pollingStopped=false。
        // よって等価性 `reachedTerminal === finalState.pollingStopped` を検証する。
        return reachedTerminal === finalState.pollingStopped;
      }),
      { numRuns: 100 },
    );
  });

  it('P4: 最終 lastSuccess は最後の SUCCESS の snapshot（無ければ初期値 null）', () => {
    fc.assert(
      fc.property(terminalAwareEventsArb, (events) => {
        const finalState = events.reduce(statusViewerReducer, initialStatusViewerState);
        // events を末尾から走査して最後の SUCCESS を取り出す。
        let lastSuccessSnapshot: CycleStatusSnapshot | null = null;
        for (let i = events.length - 1; i >= 0; i -= 1) {
          const ev = events[i];
          if (ev !== undefined && ev.type === 'SUCCESS') {
            lastSuccessSnapshot = ev.snapshot;
            break;
          }
        }
        // 参照同値性で検証（reducer は SUCCESS 時に snapshot をそのまま採用）。
        return finalState.lastSuccess === lastSuccessSnapshot;
      }),
      { numRuns: 100 },
    );
  });
});
