/**
 * サイクル履歴一覧 UI（Phase 10.7、Requirement 12.1）。
 *
 * バックエンド `GET /cycles` は startedAt 降順の全件を返す現契約のため、
 * 本コンポーネントが SPA 側で 50 件単位のページングを行う。
 *
 * 設計判断：
 *   - 一覧取得はマウント時に 1 回のみ。CRUD 後の更新は呼出側ナビゲーション
 *     に委ねる（履歴は本質的に過去のみ追加されていく性質のため）。
 *   - ページング状態は `useState` で「現在ページ番号（0-indexed）」を保持。
 *     スライスは `Array.prototype.slice` で行う（ページサイズ 50 固定）。
 *   - 19原則(b)：API エラーは `CycleApiError.serverMessage` をそのまま表示。
 */

import { useCallback, useEffect, useMemo, useState, type JSX } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { CycleApiError, CycleClient, type CycleSummary } from '../api/cycleClient';

import { formatCycleMode, formatCycleStatus } from './labels';
import { formatJst } from './formatTime';

/** 1 ページあたりの表示件数（Requirement 12.1）。 */
export const CYCLES_PAGE_SIZE = 50;

export interface CyclesListPageProps {
  /** テスト DI：未指定なら `new CycleClient()`。 */
  readonly client?: CycleClient;
  /** テスト DI：ページサイズ。既定 50。 */
  readonly pageSize?: number;
}

export function CyclesListPage(props: CyclesListPageProps = {}): JSX.Element {
  const cycleClient = useMemo(() => props.client ?? new CycleClient(), [props.client]);
  const pageSize = props.pageSize ?? CYCLES_PAGE_SIZE;
  const navigate = useNavigate();

  const [cycles, setCycles] = useState<readonly CycleSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [pageIndex, setPageIndex] = useState(0);

  const load = useCallback(() => {
    setLoading(true);
    setErrorMessage(null);
    void (async () => {
      try {
        const res = await cycleClient.list();
        setCycles(res.cycles);
        setPageIndex(0);
      } catch (err) {
        if (err instanceof CycleApiError) {
          setErrorMessage(
            `履歴の取得に失敗しました（HTTP ${err.status.toString()}）: ${err.serverMessage}`,
          );
        } else if (err instanceof Error) {
          setErrorMessage(`履歴の取得に失敗しました: ${err.message}`);
        } else {
          setErrorMessage('履歴の取得に失敗しました。');
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [cycleClient]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(cycles.length / pageSize));
  const currentPage = Math.min(pageIndex, totalPages - 1);
  const start = currentPage * pageSize;
  const end = start + pageSize;
  const pageCycles = cycles.slice(start, end);

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
        <h1>安否確認 履歴</h1>
        <Link to="/cycles/new">
          <button type="button">新規安否確認 起動</button>
        </Link>
      </header>

      {errorMessage !== null && (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {errorMessage}
        </p>
      )}

      {loading ? (
        <p role="status" aria-live="polite">
          読み込み中…
        </p>
      ) : cycles.length === 0 ? (
        <p data-testid="cycles-empty">過去の安否確認はまだありません。</p>
      ) : (
        <>
          <p style={{ color: '#6b7280', fontSize: '0.85rem', marginBottom: '0.5rem' }}>
            ※ 行をクリックすると詳細を表示します
          </p>
          <p data-testid="cycles-pagination-summary">
            全 {cycles.length} 件中 {start + 1} - {Math.min(end, cycles.length)} 件を表示 （ページ{' '}
            {currentPage + 1} / {totalPages}）
          </p>
          <figure>
          <table data-testid="cycles-table">
            <thead>
              <tr>
                <th>確認ID</th>
                <th>起動時刻</th>
                <th>ステータス</th>
                <th>Mode</th>
                <th>辞書Ver.</th>
                <th>完了時刻</th>
              </tr>
            </thead>
            <tbody>
              {pageCycles.map((c) => (
                <tr
                  key={c.cycleId}
                  data-testid={`cycle-row-${c.cycleId}`}
                  onClick={() => { navigate(`/cycles/${encodeURIComponent(c.cycleId)}`); }}
                  className="clickable-row"
                >
                  <td>{c.cycleId}</td>
                  <td>{formatJst(c.startedAt)}</td>
                  <td>{formatCycleStatus(c.status)}</td>
                  <td>{formatCycleMode(c.mode)}</td>
                  <td>{c.dictionaryVersion}</td>
                  <td>{formatJst(c.completedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </figure>
          <nav aria-label="ページ送り" style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
            <button
              type="button"
              onClick={() => {
                setPageIndex((i) => Math.max(0, i - 1));
              }}
              disabled={currentPage === 0}
              data-testid="cycles-prev-page"
            >
              前のページ
            </button>
            <button
              type="button"
              onClick={() => {
                setPageIndex((i) => Math.min(totalPages - 1, i + 1));
              }}
              disabled={currentPage >= totalPages - 1}
              data-testid="cycles-next-page"
            >
              次のページ
            </button>
          </nav>
        </>
      )}
    </section>
  );
}
