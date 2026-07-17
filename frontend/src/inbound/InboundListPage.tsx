/**
 * インバウンド着信履歴一覧 UI（Phase 10.8、Requirement 13.7）。
 *
 * 表示内容（design.md `/inbound` 設計）：
 *   - 行：受信時刻 / 発信者番号（マスキング済）/ Cycle ID / 社員名 / flow /
 *         Voice_Status / Transcript 抜粋。
 *   - 操作：録音再生 / Transcript 全文。
 *
 * 行単位の有効・無効化（CycleDetailPage の cycle 全体バナーと違って
 * inbound は「行 1 つ 1 つで着信時刻が異なる」ため行単位判定が UX 良好）：
 *   - 90 日超過（`isRetentionExpired(receivedAt, now)` true）→ 無効化 +
 *     「保管期間（90 日）超過」表示。
 *   - flow ≠ `ACTIVE_CYCLE` → 録音 / Transcript が存在しないため無効化。
 *     `NOT_REGISTERED` / `NO_CYCLE` はガイダンス再生 + 切断で本質的に
 *     録音が無く、`CYCLE_TERMINATED` も Response 更新を行わず Inbound_Contact
 *     のみ記録される（Requirement 13.6 / 13.8）ため、UX として「録音なし」
 *     を案内する。実機の事案によっては CYCLE_TERMINATED でも録音が存在する
 *     可能性があるが、UI のフロー判定として「無条件で再生不可」を採用
 *     （サーバーが 404 / 410 で確実に拒否する設計と整合）。
 *
 * ページング：サーバー側 `nextToken` を活用。SPA はトークンスタック
 * `pageTokens` を `useState` 保持して前ページに戻れる（CycleDetailPage と同パターン）。
 *
 * 設計判断：
 *   - 録音は行ごとの inline 展開（`<audio controls src=...>`）で
 *     CycleDetailPage と同型。複数行を並列再生できる。
 *   - 19原則(b)：HTTP エラーは `InboundApiError` の `serverMessage` を
 *     画面に開示。録音 410 Gone は `RecordingApiError.isGone()` で「保管
 *     期間超過」メッセージへ翻訳。それ以外の status はそのまま開示。
 *   - `nowFactory` を props 注入してテスト時刻を決定論化（cycleExpiry の
 *     90 日境界を任意の時刻で再現可能）。
 */

import { useCallback, useEffect, useMemo, useState, type JSX } from 'react';
import { Link } from 'react-router-dom';

import {
  InboundApiError,
  InboundClient,
  type InboundContactRow,
  type InboundContactsPage,
  type InboundFlow,
} from '../api/inboundClient';
import { RecordingApiError, RecordingClient, type PresignedArtifact } from '../api/recordingClient';
import { isRetentionExpired } from '../cycles/cycleExpiry';
import { formatJst } from '../cycles/formatTime';
import { formatVoiceStatus } from '../cycles/labels';

export interface InboundListPageProps {
  /** テスト DI：未指定なら `new InboundClient()`。 */
  readonly inboundClient?: InboundClient;
  /** テスト DI：未指定なら `new RecordingClient()`。 */
  readonly recordingClient?: RecordingClient;
  /** テスト DI：現在時刻ファクトリ。未指定なら `() => new Date()`。 */
  readonly nowFactory?: () => Date;
}

interface PerRowState {
  readonly recording?: PresignedArtifact;
  readonly recordingError?: string;
  readonly recordingLoading?: boolean;
}

export function InboundListPage(props: InboundListPageProps = {}): JSX.Element {
  const inboundClient = useMemo(
    () => props.inboundClient ?? new InboundClient(),
    [props.inboundClient],
  );
  const recordingClient = useMemo(
    () => props.recordingClient ?? new RecordingClient(),
    [props.recordingClient],
  );
  const nowFactory = props.nowFactory ?? ((): Date => new Date());

  const [page, setPage] = useState<InboundContactsPage | null>(null);
  const [pageTokens, setPageTokens] = useState<readonly string[]>([]);
  const [currentToken, setCurrentToken] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [rowStates, setRowStates] = useState<Readonly<Record<string, PerRowState>>>({});

  const loadPage = useCallback(
    (token: string | undefined) => {
      setLoading(true);
      setErrorMessage(null);
      void (async () => {
        try {
          const p = await inboundClient.list(token);
          setPage(p);
        } catch (err) {
          if (err instanceof InboundApiError) {
            setErrorMessage(
              `着信履歴の取得に失敗しました（HTTP ${err.status.toString()}）: ${err.serverMessage}`,
            );
          } else if (err instanceof Error) {
            setErrorMessage(`着信履歴の取得に失敗しました: ${err.message}`);
          } else {
            setErrorMessage('着信履歴の取得に失敗しました。');
          }
        } finally {
          setLoading(false);
        }
      })();
    },
    [inboundClient],
  );

  useEffect(() => {
    loadPage(undefined);
    setPageTokens([]);
    setCurrentToken(undefined);
    setRowStates({});
  }, [loadPage]);

  const goNextPage = useCallback(() => {
    if (page?.nextToken !== undefined && page.nextToken !== null) {
      const nextToken = page.nextToken;
      setPageTokens((prev) => [...prev, currentToken ?? '']);
      setCurrentToken(nextToken);
      loadPage(nextToken);
    }
  }, [page, currentToken, loadPage]);

  const goPrevPage = useCallback(() => {
    if (pageTokens.length === 0) return;
    const next = [...pageTokens];
    const prevToken = next.pop() ?? '';
    setPageTokens(next);
    const tokenArg = prevToken === '' ? undefined : prevToken;
    setCurrentToken(tokenArg);
    loadPage(tokenArg);
  }, [pageTokens, loadPage]);

  const requestRecording = useCallback(
    (row: InboundContactRow) => {
      setRowStates((prev) => ({
        ...prev,
        [row.contactId]: { ...(prev[row.contactId] ?? {}), recordingLoading: true },
      }));
      void (async () => {
        try {
          const artifact = await recordingClient.getInboundRecording(row.contactId);
          setRowStates((prev) => ({
            ...prev,
            [row.contactId]: { recording: artifact },
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
            [row.contactId]: { recordingError: message },
          }));
        }
      })();
    },
    [recordingClient],
  );

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
        <h1>着信履歴</h1>
      </header>

      {errorMessage !== null && (
        <p role="alert" style={{ color: '#b91c1c' }} data-testid="inbound-error">
          {errorMessage}
        </p>
      )}

      {loading ? (
        <p role="status" aria-live="polite">
          読み込み中…
        </p>
      ) : page === null || page.items.length === 0 ? (
        <p data-testid="inbound-empty">着信履歴はまだありません。</p>
      ) : (
        <>
          <figure>
          <table data-testid="inbound-table">
            <thead>
              <tr>
                <th>受信時刻</th>
                <th>発信者番号</th>
                <th>確認ID</th>
                <th>社員名</th>
                <th>Flow</th>
                <th>状況</th>
                <th>通話内容 抜粋</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((row) => (
                <InboundRow
                  key={row.contactId}
                  row={row}
                  state={rowStates[row.contactId]}
                  now={nowFactory()}
                  onPlay={() => {
                    requestRecording(row);
                  }}
                />
              ))}
            </tbody>
          </table>
          </figure>
          <nav aria-label="ページ送り" style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
            <button
              type="button"
              onClick={goPrevPage}
              disabled={pageTokens.length === 0}
              data-testid="inbound-prev-page"
            >
              前のページ
            </button>
            <button
              type="button"
              onClick={goNextPage}
              disabled={page.nextToken === null}
              data-testid="inbound-next-page"
            >
              次のページ
            </button>
          </nav>
        </>
      )}
    </section>
  );
}

function InboundRow({
  row,
  state,
  now,
  onPlay,
}: {
  readonly row: InboundContactRow;
  readonly state: PerRowState | undefined;
  readonly now: Date;
  readonly onPlay: () => void;
}): JSX.Element {
  const expired = isRetentionExpired(row.receivedAt, now);
  const noArtifact = !flowHasArtifact(row.flow);
  const disabled = expired || noArtifact;
  const disabledReason = noArtifact
    ? `録音なし（${flowLabel(row.flow)}）`
    : expired
      ? '保管期間（90 日）超過'
      : null;

  return (
    <tr data-testid={`inbound-row-${row.contactId}`}>
      <td>{formatJst(row.receivedAt)}</td>
      <td data-testid={`inbound-caller-${row.contactId}`}>
        {row.callerNumberMasked}
      </td>
      <td>{row.cycleId ?? '-'}</td>
      <td>{row.employeeName ?? '-'}</td>
      <td data-testid={`inbound-flow-${row.contactId}`}>
        {flowLabel(row.flow)}
      </td>
      <td>{formatVoiceStatus(row.voiceStatus)}</td>
      <td data-testid={`inbound-excerpt-${row.contactId}`}>
        {row.transcriptExcerpt ?? '-'}
      </td>
      <td>
        {disabled ? (
          <div data-testid={`inbound-disabled-${row.contactId}`}>
            <button type="button" disabled data-testid={`inbound-play-button-${row.contactId}`}>
              録音再生
            </button>{' '}
            <span aria-disabled="true" data-testid={`inbound-transcript-disabled-${row.contactId}`}>
              通話内容 全文
            </span>
            {disabledReason !== null && (
              <div data-testid={`inbound-disabled-reason-${row.contactId}`}>{disabledReason}</div>
            )}
          </div>
        ) : state?.recording !== undefined ? (
          <div data-testid={`inbound-loaded-${row.contactId}`}>
            <audio
              controls
              src={state.recording.url}
              data-testid={`inbound-audio-${row.contactId}`}
              style={{ maxWidth: '240px' }}
            >
              <track kind="captions" />
            </audio>{' '}
            <Link
              to={`/inbound/${encodeURIComponent(row.contactId)}/transcript`}
              data-testid={`inbound-transcript-link-${row.contactId}`}
            >
              通話内容 全文
            </Link>
          </div>
        ) : (
          <div>
            <button
              type="button"
              onClick={onPlay}
              disabled={state?.recordingLoading === true}
              data-testid={`inbound-play-button-${row.contactId}`}
            >
              {state?.recordingLoading === true ? '取得中…' : '録音再生'}
            </button>{' '}
            <Link
              to={`/inbound/${encodeURIComponent(row.contactId)}/transcript`}
              data-testid={`inbound-transcript-link-${row.contactId}`}
            >
              通話内容 全文
            </Link>
            {state?.recordingError !== undefined && (
              <p
                role="alert"
                data-testid={`inbound-recording-error-${row.contactId}`}
                style={{ color: '#b91c1c' }}
              >
                {state.recordingError}
              </p>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function flowHasArtifact(flow: InboundFlow): boolean {
  return flow === 'ACTIVE_CYCLE';
}

function flowLabel(flow: InboundFlow): string {
  switch (flow) {
    case 'ACTIVE_CYCLE':
      return '受付済（ACTIVE_CYCLE）';
    case 'NO_CYCLE':
      return '対象サイクルなし（NO_CYCLE）';
    case 'NOT_REGISTERED':
      return '未登録番号（NOT_REGISTERED）';
    case 'CYCLE_TERMINATED':
      return '直近サイクル終端（CYCLE_TERMINATED）';
  }
}
