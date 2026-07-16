/**
 * サイクル起動 UI（管理者画面 / Phase 10.5）。
 *
 * 対応要件：
 *   - Requirement 4.1：管理者操作で Cycle を「実行中」状態で作成し、SFN を起動する。
 *   - Requirement 4.2：画面の左側にチェックボックスを 1 個（ラベル「全員」）配置する。
 *   - Requirement 4.3：チェック有 → mode=`ALL`。
 *   - Requirement 4.4：チェック無 → mode=`UNREACHABLE_ONLY`。
 *   - Requirement 4.6：Retry_Count [0, 5] の整数。
 *   - Requirement 4.7：Retry_Interval [1, 60] 分の整数。
 *
 * 設計判断：
 *   - Phase 10.5 のタスク詳細「Retry_Count / Retry_Interval は変更不可（表示のみ）」を
 *     遵守し、本画面ではバックエンドの既定値（retryCount=3 / retryIntervalMinutes=5）を
 *     固定値として表示する。可変化は後続フェーズに委ねる。
 *   - 起動ボタン押下時に毎回新しい `Idempotency-Key`（UUID v4）を発番する。
 *     ブラウザ標準の `crypto.randomUUID()` を使い、テスト時は DI で差替可能とする。
 *   - 19原則(b)：エラーは握り潰さず、`CycleApiError.serverMessage` を
 *     そのまま画面に表示する（バックエンドの 400 / 409 / 500 を翻訳しない）。
 *   - 画面上の起動結果には `cycleId` と `dictionaryVersion` を表示する
 *     （タスク要件「結果（cycleId, dictionaryVersion）を画面表示」）。
 */

import { useCallback, useMemo, useState, type FormEvent, type JSX } from 'react';
import { Link } from 'react-router-dom';

import { CycleApiError, CycleClient, type CreateCycleResult } from '../api/cycleClient';

import { formatCycleStatus } from './labels';

/** バックエンド `cycle_api/handler.py` の既定値と一致させる。 */
export const DEFAULT_RETRY_COUNT = 3;
export const DEFAULT_RETRY_INTERVAL_MINUTES = 5;

export interface CycleStartPageProps {
  /** テスト DI：未指定なら `new CycleClient()`。 */
  readonly client?: CycleClient;
  /** テスト DI：未指定なら `crypto.randomUUID()`。 */
  readonly idempotencyKeyFactory?: () => string;
}

export function CycleStartPage({
  client,
  idempotencyKeyFactory,
}: CycleStartPageProps = {}): JSX.Element {
  const cycleClient = useMemo(() => client ?? new CycleClient(), [client]);
  const generateKey = useMemo(
    () => idempotencyKeyFactory ?? ((): string => crypto.randomUUID()),
    [idempotencyKeyFactory],
  );

  const [allChecked, setAllChecked] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [result, setResult] = useState<CreateCycleResult | null>(null);

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setServerError(null);
      setResult(null);
      setSubmitting(true);

      const mode = allChecked ? 'ALL' : 'UNREACHABLE_ONLY';
      const idempotencyKey = generateKey();

      void (async () => {
        try {
          const created = await cycleClient.create(
            {
              mode,
              retryCount: DEFAULT_RETRY_COUNT,
              retryIntervalMinutes: DEFAULT_RETRY_INTERVAL_MINUTES,
            },
            idempotencyKey,
          );
          setResult(created);
        } catch (err) {
          if (err instanceof CycleApiError) {
            setServerError(`起動失敗（HTTP ${err.status.toString()}）: ${err.serverMessage}`);
          } else if (err instanceof Error) {
            setServerError(`起動失敗: ${err.message}`);
          } else {
            setServerError('起動失敗。');
          }
        } finally {
          setSubmitting(false);
        }
      })();
    },
    [allChecked, cycleClient, generateKey],
  );

  return (
    <section>
      <h1>安否確認 起動</h1>

      <form onSubmit={onSubmit} noValidate style={{ maxWidth: '560px' }}>
        <div style={{ display: 'flex', gap: '2rem', alignItems: 'flex-start' }}>
          {/* 画面の左側に「全員」チェックボックスを 1 個配置（Requirement 4.2） */}
          <div style={{ flex: '0 0 auto' }}>
            <label
              htmlFor="cycle-all-checkbox"
              style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
            >
              <input
                id="cycle-all-checkbox"
                type="checkbox"
                checked={allChecked}
                onChange={(e) => {
                  setAllChecked(e.target.checked);
                }}
              />
              <span>全員</span>
            </label>
            <p style={{ margin: '0.5rem 0 0', color: '#374151', fontSize: '0.85em' }}>
              ON：全社員に発信
              <br />
              OFF：直近の未到達者のみに発信
            </p>
          </div>

          {/* Retry_Count / Retry_Interval は表示のみ（タスク 10.5 詳細） */}
          <div style={{ flex: '1 1 auto' }}>
            <h2 style={{ fontSize: '1rem', margin: '0 0 0.5rem' }}>リトライ設定（表示のみ）</h2>
            <dl style={{ margin: 0 }}>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <dt style={{ minWidth: '12rem' }}>リトライ回数</dt>
                <dd data-testid="cycle-retry-count" style={{ margin: 0, fontFamily: 'monospace' }}>
                  {DEFAULT_RETRY_COUNT.toString()}
                </dd>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem' }}>
                <dt style={{ minWidth: '12rem' }}>リトライ間隔（分）</dt>
                <dd
                  data-testid="cycle-retry-interval"
                  style={{ margin: 0, fontFamily: 'monospace' }}
                >
                  {DEFAULT_RETRY_INTERVAL_MINUTES.toString()}
                </dd>
              </div>
            </dl>
          </div>
        </div>

        <div style={{ marginTop: '1.5rem' }}>
          <button type="submit" disabled={submitting} style={{ padding: '0.5rem 1rem' }}>
            {submitting ? '起動中…' : '安否確認を起動する'}
          </button>
        </div>
      </form>

      {serverError !== null && (
        <p role="alert" style={{ color: '#b91c1c', marginTop: '1rem' }}>
          {serverError}
        </p>
      )}

      {result !== null && (
        <section
          aria-labelledby="cycle-start-result-title"
          style={{
            marginTop: '1.5rem',
            padding: '1rem',
            border: '1px solid #d1d5db',
            borderRadius: '0.5rem',
            background: '#f9fafb',
          }}
        >
          <h2 id="cycle-start-result-title" style={{ marginTop: 0 }}>
            {result.idempotentReplay === true
              ? '同じ Idempotency-Key で既存の安否確認が返されました'
              : '安否確認を起動しました'}
          </h2>
          <dl style={{ margin: 0 }}>
            <div style={resultRow}>
              <dt style={resultLabel}>Cycle ID</dt>
              <dd data-testid="cycle-result-cycle-id" style={resultValue}>
                {result.cycleId}
              </dd>
            </div>
            <div style={resultRow}>
              <dt style={resultLabel}>Dictionary Version</dt>
              <dd data-testid="cycle-result-dictionary-version" style={resultValue}>
                {result.dictionaryVersion.toString()}
              </dd>
            </div>
            <div style={resultRow}>
              <dt style={resultLabel}>Status</dt>
              <dd data-testid="cycle-result-status" style={resultValue}>
                {formatCycleStatus(result.status)}
              </dd>
            </div>
            <div style={resultRow}>
              <dt style={resultLabel}>Started At</dt>
              <dd data-testid="cycle-result-started-at" style={resultValue}>
                {result.startedAt}
              </dd>
            </div>
          </dl>
          <p style={{ marginTop: '1rem' }}>
            <Link to={`/cycles/${result.cycleId}/status`} data-testid="cycle-result-status-link">
              安否確認のステータスを見る
            </Link>
          </p>
        </section>
      )}
    </section>
  );
}

const resultRow: React.CSSProperties = {
  display: 'flex',
  gap: '0.5rem',
  marginTop: '0.25rem',
};

const resultLabel: React.CSSProperties = {
  minWidth: '12rem',
};

const resultValue: React.CSSProperties = {
  margin: 0,
  fontFamily: 'monospace',
};
