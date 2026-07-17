/**
 * Transcript 全文表示 UI（Phase 10.7、Requirement 12.2）。
 *
 * フロー：
 *   1. `RecordingApi` から Transcript JSON の署名付き URL を取得。
 *      - 410 Gone（90 日経過）はメッセージ表示で終了。
 *   2. 取得した URL を `fetch`（認証ヘッダ無し、S3 直アクセス）して JSON を取得。
 *   3. Amazon Transcribe 形式（`results.transcripts[0].transcript`）から全文を抽出して表示。
 *
 * 設計判断：
 *   - `RecordingApi` の応答に含まれる `url` は S3 署名付き URL のため、
 *     Authorization ヘッダを付けずに別途 `fetch` で取得する。CORS は S3
 *     バケットポリシー側で許可されている前提。
 *   - 19原則(b)：JSON パース失敗は握り潰さず、メッセージ「Transcript の解析に失敗」と
 *     生 JSON のテキストを開示する。
 *   - DI：`recordingClient` と `httpFetch`（プレーン fetch）を props で差替可能とし、
 *     テストで決定論的に検証する。
 */

import { useEffect, useMemo, useState, type JSX } from 'react';
import { Link, useParams } from 'react-router-dom';

import { RecordingApiError, RecordingClient } from '../api/recordingClient';

import { parseTranscript } from './transcriptParser';

/** プレーン fetch（認証ヘッダなし）の最小シグネチャ。テスト DI 容易化のため抽出。 */
export type PlainFetch = (input: string, init?: RequestInit) => Promise<Response>;

export interface TranscriptViewerPageProps {
  /** テスト DI：未指定なら `new RecordingClient()`。 */
  readonly recordingClient?: RecordingClient;
  /** テスト DI：S3 直接取得用プレーン fetch。未指定なら `globalThis.fetch`。 */
  readonly httpFetch?: PlainFetch;
  /** テスト DI：明示パラメータ。指定なしなら URL から取得。 */
  readonly cycleId?: string;
  readonly employeeId?: string;
  readonly seq?: string;
}

type LoadState =
  | { readonly kind: 'loading' }
  | { readonly kind: 'gone'; readonly message: string }
  | { readonly kind: 'error'; readonly message: string }
  | {
      readonly kind: 'loaded';
      readonly text: string;
      readonly confidence: number | null;
      readonly rawJson: string;
    };

export function TranscriptViewerPage(props: TranscriptViewerPageProps = {}): JSX.Element {
  const params = useParams<{ cycleId: string; employeeId: string; seq: string }>();
  const cycleId = props.cycleId ?? params.cycleId ?? '';
  const employeeId = props.employeeId ?? params.employeeId ?? '';
  const seq = props.seq ?? params.seq ?? '';

  const recordingClient = useMemo(
    () => props.recordingClient ?? new RecordingClient(),
    [props.recordingClient],
  );
  const httpFetch = useMemo<PlainFetch>(
    () => props.httpFetch ?? ((input, init) => globalThis.fetch(input, init)),
    [props.httpFetch],
  );

  const [state, setState] = useState<LoadState>({ kind: 'loading' });

  useEffect(() => {
    if (cycleId === '' || employeeId === '' || seq === '') {
      setState({ kind: 'error', message: 'Cycle ID / 社員 ID / seq のいずれかが未指定です。' });
      return;
    }
    let cancelled = false;
    setState({ kind: 'loading' });

    const safeSetState = (next: LoadState): void => {
      // 別 useEffect 起動による後追い更新を抑止するためのガード。
      // `cancelled` は cleanup callback が true に書き換える可能性があるため
      // クロージャ越しに参照する（lint の no-unnecessary-condition は誤検知のため
      // 関数を切出して narrowing を回避する）。
      if (cancelled) return;
      setState(next);
    };

    const load = async (): Promise<void> => {
      try {
        const artifact = await recordingClient.getCycleTranscript(cycleId, employeeId, seq);
        const res = await httpFetch(artifact.url);
        if (!res.ok) {
          safeSetState({
            kind: 'error',
            message: `Transcript ファイルの取得に失敗しました（HTTP ${res.status.toString()}）。`,
          });
          return;
        }
        const rawJson = await res.text();
        const parsed = parseTranscript(rawJson);
        safeSetState({ kind: 'loaded', ...parsed, rawJson });
      } catch (err) {
        if (err instanceof RecordingApiError && err.isGone()) {
          safeSetState({
            kind: 'gone',
            message: 'Transcript は保管期間（90 日）を超過したため表示できません。',
          });
        } else if (err instanceof RecordingApiError) {
          safeSetState({
            kind: 'error',
            message: `URL 取得に失敗しました（HTTP ${err.status.toString()}）: ${err.serverMessage}`,
          });
        } else if (err instanceof Error) {
          safeSetState({
            kind: 'error',
            message: `Transcript 取得に失敗しました: ${err.message}`,
          });
        } else {
          safeSetState({ kind: 'error', message: 'Transcript 取得に失敗しました。' });
        }
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [cycleId, employeeId, seq, recordingClient, httpFetch]);

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
        <h1>通話内容 全文</h1>
        {cycleId !== '' && (
          <Link to={`/cycles/${encodeURIComponent(cycleId)}`}>
            <button type="button">安否確認 詳細へ戻る</button>
          </Link>
        )}
      </header>

      <dl style={dlStyle}>
        <Row label="Cycle ID" testId="transcript-cycle-id">
          {cycleId}
        </Row>
        <Row label="社員 ID" testId="transcript-employee-id">
          {employeeId}
        </Row>
        <Row label="Seq" testId="transcript-seq">
          {seq}
        </Row>
      </dl>

      {state.kind === 'loading' && (
        <p role="status" aria-live="polite" data-testid="transcript-loading">
          Transcript を取得中…
        </p>
      )}

      {state.kind === 'gone' && (
        <p role="alert" data-testid="transcript-gone" style={warningBannerStyle}>
          {state.message}
        </p>
      )}

      {state.kind === 'error' && (
        <p role="alert" data-testid="transcript-error" style={errorBannerStyle}>
          {state.message}
        </p>
      )}

      {state.kind === 'loaded' && (
        <article aria-labelledby="transcript-text-heading" style={{ marginTop: '1rem' }}>
          <h2 id="transcript-text-heading">本文</h2>
          {state.confidence !== null && (
            <p data-testid="transcript-confidence">平均信頼度: {state.confidence.toFixed(4)}</p>
          )}
          <pre
            data-testid="transcript-text"
            style={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              padding: '1rem',
              border: '1px solid #d1d5db',
              borderRadius: '0.5rem',
              background: '#f9fafb',
            }}
          >
            {state.text}
          </pre>
        </article>
      )}
    </section>
  );
}

function Row({
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

const dlStyle: React.CSSProperties = {
  margin: 0,
};

const dlRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: '0.5rem',
  marginTop: '0.25rem',
};

const dlLabelStyle: React.CSSProperties = {
  minWidth: '8rem',
};

const dlValueStyle: React.CSSProperties = {
  margin: 0,
  fontFamily: 'monospace',
};

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
