/**
 * Status_Viewer の純粋ロジック（Phase 10.6）。
 *
 * 対応要件：
 *   - Requirement 11.1：10 秒間隔のポーリング。
 *   - Requirement 11.5：完了 / タイムアウトでポーリング停止。
 *   - Requirement 11.6：取得失敗時はエラー表示 + 直前値保持。
 *   - Requirement 18.4：縮退コンポーネント名表示。
 *
 * 対応プロパティ（Phase 13 PBT 受け皿）：
 *   - Property 18：ポーリング状態機械。
 *     `display(Q)` = `Q` 中の最後の `Success` の値、初期値は空。
 *     `status ∈ {COMPLETED, TIMEOUT}` 以降ポーリング呼出を停止。
 *     `Failure` のとき表示値は不変、エラーフラグが立つ。
 *   - Property 25：縮退表示。
 *     `renderDegraded(D)` の出力には `D` 中のすべての component 名が含まれ、
 *     `D = []` のときに限り出力に component 名は含まれない。
 *
 * 設計判断：
 *   - DI なしの純粋関数（時刻・乱数依存ゼロ）で実装する。
 *     これにより Phase 13.18 / 13.25 PBT で直接ターゲットにできる。
 *   - reducer の戻り値は新規オブジェクト（イミュータブル）。React の
 *     `useReducer` から呼出されることを前提に shallow equal で再描画判定可能。
 */

/** Cycle 上位ステータス。バックエンドが返す 4 値。 */
export type CycleTopStatus = 'RUNNING' | 'COMPLETED' | 'TIMEOUT' | 'START_FAILED';

/** Voice_Status の取り得る値（個別行表示用）。 */
export type VoiceStatusValue =
  | 'SAFE'
  | 'INJURED'
  | 'UNAVAILABLE'
  | 'OTHER'
  | 'UNREACHABLE'
  | 'PENDING';

/** 集計値。 */
export interface CycleStatusSummary {
  readonly targetTotal: number;
  readonly dispatched: number;
  readonly responded: number;
  readonly unreachable: number;
  /** SAFE / INJURED / UNAVAILABLE / OTHER / UNREACHABLE / PENDING の人数。 */
  readonly byStatus: Readonly<Record<string, number>>;
}

/** 個別社員行。 */
export interface CycleStatusItem {
  readonly employeeId: string;
  readonly name: string;
  readonly currentStatus: VoiceStatusValue;
  readonly callAttempts: number;
  /** ISO 8601 または null（未応答）。 */
  readonly lastResponseAt: string | null;
  /** Transcript 抜粋（バックエンドで先頭 100 文字に切詰済の想定）。 */
  readonly transcriptExcerpt: string;
}

/** 縮退情報。 */
export interface CycleDegradedEntry {
  readonly component: string;
  /** ISO 8601。 */
  readonly since: string;
}

/** `GET /cycles/{id}/status` の正規化済レスポンス。 */
export interface CycleStatusSnapshot {
  readonly cycleId: string;
  readonly status: CycleTopStatus;
  readonly summary: CycleStatusSummary;
  readonly items: readonly CycleStatusItem[];
  readonly degraded: readonly CycleDegradedEntry[];
}

/** Status_Viewer の表示状態。 */
export interface StatusViewerState {
  /** 最後に取得に成功したスナップショット。初期値は null。 */
  readonly lastSuccess: CycleStatusSnapshot | null;
  /** 直近のポーリングが失敗していたら true（次の SUCCESS で false に戻る）。 */
  readonly errorFlag: boolean;
  /** 終端ステータス到達後 true。以降ポーリングは発行されない。 */
  readonly pollingStopped: boolean;
}

/** 初期状態。 */
export const initialStatusViewerState: StatusViewerState = {
  lastSuccess: null,
  errorFlag: false,
  pollingStopped: false,
};

/** Reducer に流すイベント。 */
export type StatusViewerEvent =
  | { readonly type: 'SUCCESS'; readonly snapshot: CycleStatusSnapshot }
  | { readonly type: 'FAILURE' };

/**
 * ポーリング状態機械（Property 18）。
 *
 * @param state 現在の表示状態。
 * @param event 取得結果イベント。
 * @returns 新しい表示状態。
 */
export function statusViewerReducer(
  state: StatusViewerState,
  event: StatusViewerEvent,
): StatusViewerState {
  if (event.type === 'SUCCESS') {
    return {
      lastSuccess: event.snapshot,
      errorFlag: false,
      pollingStopped: isTerminalStatus(event.snapshot.status),
    };
  }
  // FAILURE：lastSuccess は不変、エラーフラグを立てる。
  // pollingStopped は SUCCESS で停止済の場合は保持（停止後の遅延失敗は無効化）。
  return {
    lastSuccess: state.lastSuccess,
    errorFlag: true,
    pollingStopped: state.pollingStopped,
  };
}

/**
 * 終端ステータス判定（Property 18 の停止条件）。
 *
 * バックエンド `CycleFinalizer` は Map 完了で `COMPLETED`、60 分タイムアウトで
 * `TIMEOUT` を Cycle に書込む。SPA はこの 2 つを終端として扱いポーリングを停止する。
 * `START_FAILED` は起動時の異常状態で、再ポーリングしても回復しないため終端扱いする。
 */
export function isTerminalStatus(status: CycleTopStatus): boolean {
  return status === 'COMPLETED' || status === 'TIMEOUT' || status === 'START_FAILED';
}

/**
 * 縮退表示（Property 25）。
 *
 * 入力 `degraded[].component` を順序保持で文字列配列に展開する。
 * 空配列の場合は空配列を返し、これにより出力には component 名が一切含まれなくなる。
 *
 * @param degraded 縮退情報の配列（API レスポンスの `degraded` フィールド）。
 * @returns コンポーネント名の配列。
 */
export function renderDegraded(degraded: readonly CycleDegradedEntry[]): readonly string[] {
  return degraded.map((d) => d.component);
}
