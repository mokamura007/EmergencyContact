/**
 * サイクル詳細 + 社員別 Response 履歴 UI（Phase 10.7、Requirement 12.1〜12.3）。
 *
 * 表示内容：
 *   - Cycle 情報（cycleId / status / mode / startedAt / completedAt / dictionaryVersion）。
 *   - 社員別 Response 一覧（50 件ページング、降順）：employeeId / employeeName /
 *     voiceStatus / callResultCode / retryCount / lastCalledAt / transcriptExcerpt。
 *   - 各行で「録音再生」ボタンと「Transcript 全文」リンクを提供。
 *     - 90 日内：ボタン押下で `RecordingApi` から署名付き URL を取得し、
 *       `<audio controls>` で再生。Transcript リンクは別画面 `TranscriptViewerPage` へ。
 *     - 90 日超過：ボタン・リンクともに無効化し「保管期間（90 日）を超過しました」
 *       メッセージを表示。`isRetentionExpired` で SPA 側プリフライト判定する。
 *     - retryCount = 0（未架電 / 一度も応答無し）の場合は録音 / Transcript
 *       がそもそも存在しないため、両方を無効化して「録音なし」を表示。
 *
 * 設計判断：
 *   - ページングはサーバー側 `nextToken` を活用する（response_api の現契約）。
 *     SPA はトークンスタックを保持して「前のページ」も実現する。
 *   - 録音 / Transcript の `seq` は response_api が露出する `retryCount`
 *     を使う（design.md の S3 キー `recordings/{cycleId}/{employeeId}/{seq}.wav` と
 *     合わせるため、最後の通話 seq の代理値として現状ベストエフォート）。
 *   - 19原則(b)：サーバー応答が 410 だった場合は `RecordingApiError.isGone()`
 *     を判別し、保管期間超過メッセージを表示する（押下時の最終確認）。
 *   - 録音 URL 取得後の状態管理は `Map<employeeId, PresignedArtifact>` で
 *     行い、複数社員の音声を順次再生できるようにする。
 */

import { useCallback, useEffect, useMemo, useState, type JSX } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  CycleApiError,
  CycleClient,
  type CycleDetail,
  type CycleResponseRow,
  type CycleResponsesPage,
} from '../api/cycleClient';
import { RecordingApiError, RecordingClient, type PresignedArtifact } from '../api/recordingClient';

import { isRetentionExpired } from './cycleExpiry';
import { formatJst } from './formatTime';
import { formatCycleMode, formatCycleStatus, formatVoiceStatus } from './labels';

export interface CycleDetailPageProps {
  /** テスト DI：未指定なら `new CycleClient()`。 */
  readonly cycleClient?: CycleClient;
  /** テスト DI：未指定なら `new RecordingClient()`。 */
  readonly recordingClient?: RecordingClient;
  /** テスト DI：明示的に Cycle ID を渡す（指定なしなら URL パラメータから取得）。 */
  readonly cycleId?: string;
  /** テスト DI：現在時刻ファクトリ。未指定なら `() => new Date()`。 */
  readonly nowFactory?: () => Date;
}

interface PerRowState {
  readonly recording?: PresignedArtifact;
  readonly recordingError?: string;
  readonly recordingLoading?: boolean;
}

export function CycleDetailPage(props: CycleDetailPageProps = {}): JSX.Element {
  const params = useParams<{ cycleId: string }>();
  const cycleId = props.cycleId ?? params.cycleId ?? '';
  const cycleClient = useMemo(() => props.cycleClient ?? new CycleClient(), [props.cycleClient]);
  const recordingClient = useMemo(
    () => props.recordingClient ?? new RecordingClient(),
    [props.recordingClient],
  );
  const nowFactory = props.nowFactory ?? ((): Date => new Date());

  const [detail, setDetail] = useState<CycleDetail | null>(null);
  const [page, setPage] = useState<CycleResponsesPage | null>(null);
  const [pageTokens, setPageTokens] = useState<readonly string[]>([]); // 履歴スタック
  const [currentToken, setCurrentToken] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [rowStates, setRowStates] = useState<Readonly<Record<string, PerRowState>>>({});
  const [responseSortKey, setResponseSortKey] = useState<'contact' | 'status' | null>(null);
  const [responseSortDir, setResponseSortDir] = useState<'asc' | 'desc'>('asc');

  const sortedItems = useMemo(() => {
    if (page === null || responseSortKey === null) return page?.items ?? [];
    return [...page.items].sort((a, b) => {
      let cmp = 0;
      if (responseSortKey === 'contact') {
        const aVal = isContacted(a) ? 1 : 0;
        const bVal = isContacted(b) ? 1 : 0;
        cmp = aVal - bVal;
      } else {
        const aVal = a.voiceStatus ?? '';
        const bVal = b.voiceStatus ?? '';
        cmp = aVal.localeCompare(bVal);
      }
      return responseSortDir === 'asc' ? cmp : -cmp;
    });
  }, [page, responseSortKey, responseSortDir]);

  const toggleResponseSort = useCallback((key: 'contact' | 'status') => {
    setResponseSortKey((prev) => {
      if (prev === key) {
        setResponseSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setResponseSortDir('asc');
      }
      return key;
    });
  }, []);

  const loadDetail = useCallback(() => {
    if (cycleId === '') return;
    void (async () => {
      try {
        const d = await cycleClient.getDetail(cycleId);
        setDetail(d);
      } catch (err) {
        if (err instanceof CycleApiError) {
          setErrorMessage(
            `詳細の取得に失敗しました（HTTP ${err.status.toString()}）: ${err.serverMessage}`,
          );
        } else if (err instanceof Error) {
          setErrorMessage(`詳細の取得に失敗しました: ${err.message}`);
        } else {
          setErrorMessage('詳細の取得に失敗しました。');
        }
      }
    })();
  }, [cycleClient, cycleId]);

  const loadResponses = useCallback(
    (token: string | undefined) => {
      if (cycleId === '') return;
      setLoading(true);
      setErrorMessage(null);
      void (async () => {
        try {
          const p = await cycleClient.listResponses(cycleId, token);
          setPage(p);
        } catch (err) {
          if (err instanceof CycleApiError) {
            setErrorMessage(
              `Response 一覧の取得に失敗しました（HTTP ${err.status.toString()}）: ${err.serverMessage}`,
            );
          } else if (err instanceof Error) {
            setErrorMessage(`Response 一覧の取得に失敗しました: ${err.message}`);
          } else {
            setErrorMessage('Response 一覧の取得に失敗しました。');
          }
        } finally {
          setLoading(false);
        }
      })();
    },
    [cycleClient, cycleId],
  );

  useEffect(() => {
    loadDetail();
    loadResponses(undefined);
    setPageTokens([]);
    setCurrentToken(undefined);
    setRowStates({});
  }, [loadDetail, loadResponses]);

  const goNextPage = useCallback(() => {
    if (page?.nextToken !== undefined && page.nextToken !== null) {
      const nextToken = page.nextToken;
      setPageTokens((prev) => [...prev, currentToken ?? '']);
      setCurrentToken(nextToken);
      loadResponses(nextToken);
    }
  }, [page, currentToken, loadResponses]);

  const goPrevPage = useCallback(() => {
    if (pageTokens.length === 0) return;
    const next = [...pageTokens];
    const prevToken = next.pop() ?? '';
    setPageTokens(next);
    const tokenArg = prevToken === '' ? undefined : prevToken;
    setCurrentToken(tokenArg);
    loadResponses(tokenArg);
  }, [pageTokens, loadResponses]);

  const requestRecording = useCallback(
    (row: CycleResponseRow) => {
      const seq = row.retryCount > 0 ? row.retryCount.toString() : '1';
      setRowStates((prev) => ({
        ...prev,
        [row.employeeId]: { ...(prev[row.employeeId] ?? {}), recordingLoading: true },
      }));
      void (async () => {
        try {
          const artifact = await recordingClient.getCycleRecording(cycleId, row.employeeId, seq);
          setRowStates((prev) => ({
            ...prev,
            [row.employeeId]: { recording: artifact },
          }));
        } catch (err) {
          let message: string;
          if (err instanceof RecordingApiError) {
            message = err.isGone()
              ? '録音は保管期間（90 日）を超過したため再生できません。'
              : `録音の取得に失敗しました（HTTP ${err.status.toString()}）: ${err.serverMessage}`;
          } else if (err instanceof Error) {
            message = `録音の取得に失敗しました: ${err.message}`;
          } else {
            message = '録音の取得に失敗しました。';
          }
          setRowStates((prev) => ({
            ...prev,
            [row.employeeId]: { recordingError: message },
          }));
        }
      })();
    },
    [recordingClient, cycleId],
  );

  if (cycleId === '') {
    return (
      <section>
        <h1>安否確認 詳細</h1>
        <p role="alert">確認IDが指定されていません。</p>
      </section>
    );
  }

  const expired = detail !== null && isRetentionExpired(detail.startedAt, nowFactory());

  return (
    <section>
      <header
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '1rem',
        }}
      >
        <h1>安否確認 詳細</h1>
        <Link to="/cycles">
          <button type="button">一覧へ戻る</button>
        </Link>
      </header>

      {errorMessage !== null && (
        <p role="alert" style={{ color: '#b91c1c' }} data-testid="detail-error">
          {errorMessage}
        </p>
      )}

      {detail === null ? (
        <p role="status" aria-live="polite">
          安否確認情報を取得中…
        </p>
      ) : (
        <details style={blockStyle}>
          <summary style={{ cursor: 'pointer', fontWeight: 'bold', fontSize: '1.1rem' }}>安否確認詳細情報</summary>
          <dl style={dlStyle}>
            <InfoRow label="確認ID" testId="detail-cycle-id">
              {detail.cycleId}
            </InfoRow>
            <InfoRow label="ステータス" testId="detail-status">
              {formatCycleStatus(detail.status)}
            </InfoRow>
            <InfoRow label="Mode" testId="detail-mode">
              {formatCycleMode(detail.mode)}
            </InfoRow>
            <InfoRow label="起動時刻" testId="detail-started-at">
              {formatJst(detail.startedAt)}
            </InfoRow>
            <InfoRow label="完了時刻" testId="detail-completed-at">
              {formatJst(detail.completedAt)}
            </InfoRow>
            <InfoRow label="辞書バージョン" testId="detail-dictionary-version">
              {detail.dictionaryVersion.toString()}
            </InfoRow>
          </dl>
          <p style={{ color: '#6b7280', fontSize: '0.8rem', marginTop: '0.5rem' }}>
            ※ 辞書バージョン：この安否確認で通話内容の判定に使用されたキーワード辞書の版番号です
          </p>
          {expired && (
            <p
              role="status"
              data-testid="detail-retention-expired-banner"
              style={warningBannerStyle}
            >
              起動から 90 日が経過しているため、録音 / 通話内容は再生できません。
            </p>
          )}
        </details>
      )}

      <section aria-labelledby="responses-heading" style={blockStyle}>
        <h2 id="responses-heading">社員別 反応 一覧</h2>
        {loading ? (
          <p role="status" aria-live="polite">
            読み込み中…
          </p>
        ) : page === null || page.items.length === 0 ? (
          <p data-testid="responses-empty">反応はまだ登録されていません。</p>
        ) : (
          <>
            <figure>
            <table data-testid="responses-table">
              <thead>
                <tr>
                  <th>氏名</th>
                  <th style={{ cursor: 'pointer' }} onClick={() => { toggleResponseSort('contact'); }}>連絡{responseSortKey === 'contact' ? (responseSortDir === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                  <th style={{ cursor: 'pointer' }} onClick={() => { toggleResponseSort('status'); }}>状況{responseSortKey === 'status' ? (responseSortDir === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                  <th>通話内容 抜粋</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((row) => {
                  const seq = row.retryCount > 0 ? row.retryCount.toString() : '1';
                  const hasRecording = row.retryCount > 0;
                  const rowState = rowStates[row.employeeId];
                  const contacted = isContacted(row);
                  return (
                    <tr key={row.employeeId} data-testid={`response-row-${row.employeeId}`}>
                      <td>{row.employeeName ?? '-'}</td>
                      <td>{contacted ? '✅ 済' : '❌ 未'}</td>
                      <td>{formatVoiceStatus(row.voiceStatus)}</td>
                      <td data-testid={`response-excerpt-${row.employeeId}`}>
                        {row.transcriptExcerpt ?? '-'}
                      </td>
                      <td>
                        <RecordingControls
                          row={row}
                          seq={seq}
                          hasRecording={hasRecording}
                          expired={expired}
                          cycleId={cycleId}
                          state={rowState}
                          onPlay={() => {
                            requestRecording(row);
                          }}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </figure>
            <nav
              aria-label="ページ送り"
              style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}
            >
              <button
                type="button"
                onClick={goPrevPage}
                disabled={pageTokens.length === 0}
                data-testid="responses-prev-page"
              >
                前のページ
              </button>
              <button
                type="button"
                onClick={goNextPage}
                disabled={page.nextToken === null}
                data-testid="responses-next-page"
              >
                次のページ
              </button>
            </nav>
          </>
        )}
      </section>
    </section>
  );
}

/**
 * 連絡済み判定：voiceStatus に基づく。
 * - SAFE / INJURED / UNAVAILABLE / OTHER → 連絡済み（通話が成立した）
 * - PENDING / UNREACHABLE → 未連絡
 */
function isContacted(row: CycleResponseRow): boolean {
  const status = row.voiceStatus;
  if (status === null) return false;
  return status !== 'PENDING' && status !== 'UNREACHABLE';
}

function RecordingControls({
  row,
  seq,
  hasRecording,
  expired,
  cycleId,
  state,
  onPlay,
}: {
  readonly row: CycleResponseRow;
  readonly seq: string;
  readonly hasRecording: boolean;
  readonly expired: boolean;
  readonly cycleId: string;
  readonly state: PerRowState | undefined;
  readonly onPlay: () => void;
}): JSX.Element {
  const disabled = expired || !hasRecording;
  const disabledReason = !hasRecording
    ? '録音なし（未架電のため）'
    : expired
      ? '保管期間（90 日）超過'
      : null;

  if (disabled) {
    return (
      <div data-testid={`recording-disabled-${row.employeeId}`}>
        <button type="button" disabled data-testid={`play-button-${row.employeeId}`}>
          録音再生
        </button>{' '}
        <span aria-disabled="true" data-testid={`transcript-disabled-${row.employeeId}`}>
          通話内容 全文
        </span>
        {disabledReason !== null && (
          <div data-testid={`recording-disabled-reason-${row.employeeId}`}>{disabledReason}</div>
        )}
      </div>
    );
  }

  if (state?.recording !== undefined) {
    return (
      <div data-testid={`recording-loaded-${row.employeeId}`}>
        <audio
          controls
          src={state.recording.url}
          data-testid={`audio-${row.employeeId}`}
          style={{ maxWidth: '240px' }}
        >
          <track kind="captions" />
        </audio>{' '}
        <Link
          to={`/cycles/${encodeURIComponent(cycleId)}/transcripts/${encodeURIComponent(
            row.employeeId,
          )}/${encodeURIComponent(seq)}`}
          data-testid={`transcript-link-${row.employeeId}`}
        >
          通話内容 全文
        </Link>
      </div>
    );
  }

  return (
    <div>
      <button
        type="button"
        onClick={onPlay}
        disabled={state?.recordingLoading === true}
        data-testid={`play-button-${row.employeeId}`}
      >
        {state?.recordingLoading === true ? '取得中…' : '録音再生'}
      </button>{' '}
      <Link
        to={`/cycles/${encodeURIComponent(cycleId)}/transcripts/${encodeURIComponent(
          row.employeeId,
        )}/${encodeURIComponent(seq)}`}
        data-testid={`transcript-link-${row.employeeId}`}
      >
        通話内容 全文
      </Link>
      {state?.recordingError !== undefined && (
        <p
          role="alert"
          data-testid={`recording-error-${row.employeeId}`}
          style={{ color: '#b91c1c' }}
        >
          {state.recordingError}
        </p>
      )}
    </div>
  );
}

function InfoRow({
  label,
  testId,
  children,
}: {
  readonly label: string;
  readonly testId: string;
  readonly children: string;
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

const warningBannerStyle: React.CSSProperties = {
  padding: '0.75rem 1rem',
  marginTop: '1rem',
  border: '1px solid #d97706',
  background: '#fffbeb',
  color: '#78350f',
  borderRadius: '0.5rem',
};
