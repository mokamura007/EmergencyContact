/**
 * インバウンド Transcript 全文表示 UI（Phase 10.8、Requirement 13.7）。
 *
 * フロー（`cycles/TranscriptViewerPage` と同型、cycleId/employeeId/seq の
 * 代わりに contactId 単独）：
 *   1. `RecordingClient.getInboundTranscript(contactId)` で署名付き URL を取得。
 *      - 410 Gone（90 日経過）はメッセージ表示で終了。
 *   2. 取得した URL を `httpFetch`（認証ヘッダ無し、S3 直アクセス）で JSON 取得。
 *   3. `parseTranscript`（cycles 配下の純粋関数を再利用）で本文 + 平均信頼度抽出。
 *
 * 設計判断：
 *   - 19原則(a) DRY：`parseTranscript` / `PlainFetch` / `safeSetState` パターンを
 *     `cycles/TranscriptViewerPage` から引継ぐ。インバウンド固有の差分は URL
 *     パラメータが `contactId` 1 件のみという点と、戻り先リンクが `/inbound`
 *     という点のみ。
 *   - 19原則(b)：JSON パース失敗 / S3 5xx / RecordingApiError 各種は握り潰さず
 *     エラーバナーで開示。410 のみ「保管期間超過」メッセージへ翻訳。
 */

import { useEffect, useMemo, useState, type JSX } from 'react';
import { Link, useParams } from 'react-router-dom';

import { RecordingApiError, RecordingClient } from '../api/recordingClient';
import { parseTranscript } from '../cycles/transcriptParser';
import type { PlainFetch } from '../cycles/TranscriptViewerPage';

export interface InboundTranscriptViewerPageProps {
  /** テスト DI：未指定なら `new RecordingClient()`。 */
  readonly recordingClient?: RecordingClient;
  /** テスト DI：S3 直接取得用プレーン fetch。未指定なら `globalThis.fetch`。 */
  readonly httpFetch?: PlainFetch;
  /** テスト DI：明示パラメータ。指定なしなら URL から取得。 */
  readonly contactId?: string;
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

export function InboundTranscriptViewerPage(
  props: InboundTranscriptViewerPageProps = {},
): JSX.Element {
  const params = useParams<{ contactId: string }>();
  const contactId = props.contactId ?? params.contactId ?? '';

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
    if (contactId === '') {
      setState({ kind: 'error', message: 'Contact ID が未指定です。' });
      return;
    }
    let cancelled = false;
    setState({ kind: 'loading' });

    const safeSetState = (next: LoadState): void => {
      if (cancelled) return;
      setState(next);
    };

    const load = async (): Promise<void> => {
      try {
        const artifact = await recordingClient.getInboundTranscript(contactId);
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
  }, [contactId, recordingClient, httpFetch]);

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
        <h1>着信 通話内容 全文</h1>
        <Link to="/inbound">
          <button type="button">着信履歴へ戻る</button>
        </Link>
      </header>

      <dl style={dlStyle}>
        <Row label="Contact ID" testId="inbound-transcript-contact-id">
          {contactId}
        </Row>
      </dl>

      {state.kind === 'loading' && (
        <p role="status" aria-live="polite" data-testid="inbound-transcript-loading">
          Transcript を取得中…
        </p>
      )}

      {state.kind === 'gone' && (
        <p role="alert" data-testid="inbound-transcript-gone" style={warningBannerStyle}>
          {state.message}
        </p>
      )}

      {state.kind === 'error' && (
        <p role="alert" data-testid="inbound-transcript-error" style={errorBannerStyle}>
          {state.message}
        </p>
      )}

      {state.kind === 'loaded' && (
        <article aria-labelledby="inbound-transcript-text-heading" style={{ marginTop: '1rem' }}>
          <h2 id="inbound-transcript-text-heading">本文</h2>
          {state.confidence !== null && (
            <p data-testid="inbound-transcript-confidence">
              平均信頼度: {state.confidence.toFixed(4)}
            </p>
          )}
          <pre
            data-testid="inbound-transcript-text"
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
