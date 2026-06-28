/**
 * サイクルステータス見える化 UI（Phase 10.6）。
 *
 * 対応要件：
 *   - Requirement 11.1：10 秒間隔（±1 秒以内）で `/cycles/{id}/status` を取得。
 *   - Requirement 11.2：targetTotal / dispatched / responded / unreachable / byStatus 表示。
 *   - Requirement 11.3：個別社員ごとに最新 Voice_Status / 発信回数 / 最終応答時刻 /
 *                       Transcript 抜粋（先頭 100 文字）を一覧表示。
 *   - Requirement 11.5：COMPLETED / TIMEOUT 到達でポーリング停止、最後の値を保持表示。
 *   - Requirement 11.6：取得失敗時はエラーバナー + 直前値保持、次周期で再取得継続。
 *   - Requirement 18.4：縮退コンポーネント名を警告バナー表示（Property 25）。
 *
 * 設計判断：
 *   - 状態管理は `useReducer(statusViewerReducer, initialStatusViewerState)` に集約。
 *     reducer は純粋関数として切出してあり、Phase 13.18 PBT でそのままターゲット化可能。
 *   - 10 秒間隔の `setInterval` ポーリング。`AbortController` を用い、前回呼出が
 *     未完了のうちに次の tick が来た場合は前回を abort して重複呼出を抑止する
 *     （Requirement 11.1「10 秒間隔（±1 秒以内）」のスループット保護）。
 *   - 終端ステータス到達（reducer の `pollingStopped`）後は useEffect から
 *     setInterval を解除し、再起動しない。FAILURE は polling 継続させて
 *     直前値表示 + エラーバナーで Requirement 11.6 を満たす。
 *   - DI：`client` と `pollingIntervalMs` と `cycleId` をすべて props で差替可能とし、
 *     テスト時は `vi.useFakeTimers()` でタイマー制御する。
 */

import { useEffect, useMemo, useReducer, useRef, type JSX } from 'react';
import { useParams } from 'react-router-dom';

import { CycleClient, type CycleStatusSnapshot } from '../api/cycleClient';

import { initialStatusViewerState, renderDegraded, statusViewerReducer } from './statusReducer';

/** 既定ポーリング間隔（Requirement 11.1）。 */
export const STATUS_POLLING_INTERVAL_MS = 10_000;

export interface CycleStatusPageProps {
  /** テスト DI：未指定なら `new CycleClient()`。 */
  readonly client?: CycleClient;
  /** テスト DI：明示的に Cycle ID を渡す（指定なしなら URL パラメータから取得）。 */
  readonly cycleId?: string;
  /** テスト DI：ポーリング間隔（ms）。未指定なら 10 秒。 */
  readonly pollingIntervalMs?: number;
}

export function CycleStatusPage(props: CycleStatusPageProps = {}): JSX.Element {
  const params = useParams<{ cycleId: string }>();
  const cycleId = props.cycleId ?? params.cycleId ?? '';
  const cycleClient = useMemo(() => props.client ?? new CycleClient(), [props.client]);
  const intervalMs = props.pollingIntervalMs ?? STATUS_POLLING_INTERVAL_MS;

  const [state, dispatch] = useReducer(statusViewerReducer, initialStatusViewerState);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (cycleId === '') return undefined;
    if (state.pollingStopped) return undefined; // 終端到達後は再起動しない

    let cancelled = false;

    const tick = async (): Promise<void> => {
      // 重複呼出抑止：前回の進行中 fetch を中止してから新規発行する。
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const snapshot: CycleStatusSnapshot = await cycleClient.getStatus(
          cycleId,
          controller.signal,
        );
        if (cancelled) return;
        dispatch({ type: 'SUCCESS', snapshot });
      } catch (err) {
        if (cancelled) return;
        if (isAbortError(err)) return; // 自発的な中止は失敗扱いしない
        dispatch({ type: 'FAILURE' });
      }
    };

    // 初回ロード時に即座に 1 回取得し、以降は intervalMs 間隔で繰返す。
    void tick();
    const handle = setInterval(() => {
      void tick();
    }, intervalMs);

    return () => {
      cancelled = true;
      clearInterval(handle);
      abortRef.current?.abort();
    };
  }, [cycleId, cycleClient, intervalMs, state.pollingStopped]);

  if (cycleId === '') {
    return (
      <section>
        <h1>サイクルステータス</h1>
        <p role="alert">Cycle ID が指定されていません。</p>
      </section>
    );
  }

  const snapshot = state.lastSuccess;
  const degradedNames = snapshot === null ? [] : renderDegraded(snapshot.degraded);

  return (
    <section>
      <h1>サイクルステータス</h1>
      <p>
        Cycle ID: <span data-testid="cycle-status-id">{cycleId}</span>
      </p>

      {state.errorFlag && (
        <div role="alert" data-testid="status-error-banner" style={errorBannerStyle}>
          ステータスの取得に失敗しました。直前の値を表示しています。次回ポーリングで再取得します。
        </div>
      )}

      {degradedNames.length > 0 && (
        <div role="status" data-testid="status-degraded-banner" style={warningBannerStyle}>
          縮退中のコンポーネント：{degradedNames.join(' / ')}
        </div>
      )}

      {snapshot === null ? (
        <p data-testid="status-empty">ステータス取得を待機中…</p>
      ) : (
        <>
          <p>
            Cycle 状態：
            <span data-testid="status-top">{snapshot.status}</span>
            {state.pollingStopped && (
              <span data-testid="status-polling-stopped"> （ポーリング停止）</span>
            )}
          </p>

          <section aria-labelledby="summary-heading" style={blockStyle}>
            <h2 id="summary-heading">集計</h2>
            <dl style={dlStyle}>
              <SummaryRow label="対象者総数" testId="summary-target-total">
                {snapshot.summary.targetTotal}
              </SummaryRow>
              <SummaryRow label="発信完了数" testId="summary-dispatched">
                {snapshot.summary.dispatched}
              </SummaryRow>
              <SummaryRow label="応答取得数" testId="summary-responded">
                {snapshot.summary.responded}
              </SummaryRow>
              <SummaryRow label="未到達数" testId="summary-unreachable">
                {snapshot.summary.unreachable}
              </SummaryRow>
            </dl>
            <h3>ステータス別内訳</h3>
            <ul data-testid="summary-by-status" style={listStyle}>
              {Object.entries(snapshot.summary.byStatus).map(([k, v]) => (
                <li key={k} data-testid={`status-count-${k}`}>
                  {k}: {v}
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="items-heading" style={blockStyle}>
            <h2 id="items-heading">個別社員一覧</h2>
            {snapshot.items.length === 0 ? (
              <p data-testid="status-items-empty">対象者がいません。</p>
            ) : (
              <table data-testid="status-items-table" style={tableStyle}>
                <thead>
                  <tr>
                    <th>社員 ID</th>
                    <th>氏名</th>
                    <th>Voice_Status</th>
                    <th>発信回数</th>
                    <th>最終応答時刻</th>
                    <th>Transcript 抜粋</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.items.map((it) => (
                    <tr key={it.employeeId} data-testid={`status-item-${it.employeeId}`}>
                      <td>{it.employeeId}</td>
                      <td>{it.name}</td>
                      <td data-testid={`item-status-${it.employeeId}`}>{it.currentStatus}</td>
                      <td>{it.callAttempts}</td>
                      <td>{it.lastResponseAt ?? '未応答'}</td>
                      <td data-testid={`item-excerpt-${it.employeeId}`}>{it.transcriptExcerpt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </section>
  );
}

function SummaryRow({
  label,
  testId,
  children,
}: {
  readonly label: string;
  readonly testId: string;
  readonly children: number;
}): JSX.Element {
  return (
    <div style={dlRowStyle}>
      <dt style={dlLabelStyle}>{label}</dt>
      <dd data-testid={testId} style={dlValueStyle}>
        {children}
      </dd>
    </div>
  );
}

function isAbortError(err: unknown): boolean {
  if (err instanceof DOMException && err.name === 'AbortError') return true;
  if (err instanceof Error && err.name === 'AbortError') return true;
  return false;
}

const errorBannerStyle: React.CSSProperties = {
  padding: '0.75rem 1rem',
  marginTop: '1rem',
  border: '1px solid #b91c1c',
  background: '#fef2f2',
  color: '#7f1d1d',
  borderRadius: '0.5rem',
};

const warningBannerStyle: React.CSSProperties = {
  padding: '0.75rem 1rem',
  marginTop: '1rem',
  border: '1px solid #d97706',
  background: '#fffbeb',
  color: '#78350f',
  borderRadius: '0.5rem',
};

const blockStyle: React.CSSProperties = {
  marginTop: '1.5rem',
};

const dlStyle: React.CSSProperties = {
  margin: 0,
};

const dlRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: '0.5rem',
  marginTop: '0.25rem',
};

const dlLabelStyle: React.CSSProperties = {
  minWidth: '10rem',
};

const dlValueStyle: React.CSSProperties = {
  margin: 0,
  fontFamily: 'monospace',
};

const listStyle: React.CSSProperties = {
  margin: '0.5rem 0 0',
  paddingLeft: '1.5rem',
};

const tableStyle: React.CSSProperties = {
  marginTop: '0.5rem',
  borderCollapse: 'collapse',
  width: '100%',
};
