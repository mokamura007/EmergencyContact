/**
 * 社員一覧 + 削除動線 + Cognito 削除動線（管理者画面 / Phase 10.4 / Task 15.16）。
 *
 * 対応要件：
 *   - Requirement 2.1〜2.7（一覧 / 追加 / 編集 / 削除の動線、論理削除）
 *   - Requirement 15.3 / 15.4 / NFR3（Task 15.16：退職管理者の Cognito アカウント削除動線）
 *
 * 設計判断：
 *   - 一覧はマウント時に GET /employees を 1 回だけ叩く。
 *   - 「論理削除済も表示」トグル ON で `GET /employees?includeDeleted=true` を再取得。
 *     論理削除済社員はバックエンド側で `deleted=true` を含めて返却される。
 *   - Cognito 削除ボタンは「論理削除済 AND 管理者ロール（isAdmin=true）」の行にのみ表示。
 *     生きている社員には表示しない（運用順序：先に論理削除 → 後に Cognito 削除）。
 *   - Cognito 削除は不可逆操作のため、確認ダイアログで明示する。
 *   - 削除確認はモーダルダイアログとして実装（Test Library で `role="dialog"` を
 *     検証しやすく、UX 上も誤操作の二重防止）。
 *   - 19原則(b)：API エラーは `EmployeeApiError.serverMessage` をそのまま画面に表示。
 */

import { useCallback, useEffect, useMemo, useState, type JSX } from 'react';
import { Link } from 'react-router-dom';

import { EmployeeApiError, EmployeeClient, type EmployeeSummary } from '../api/employeeClient';

import { e164ToDomestic } from './validation';

export interface EmployeeListPageProps {
  /** テスト DI：未指定なら `new EmployeeClient()`。 */
  readonly client?: EmployeeClient;
}

type ConfirmAction = 'delete' | 'cognitoDelete';

interface ConfirmTarget {
  readonly employeeId: string;
  readonly name: string;
  readonly action: ConfirmAction;
}

type SortKey = 'name' | 'phoneNumber' | 'isAdmin' | 'status';
type SortDir = 'asc' | 'desc';

function compareEmployees(a: EmployeeSummary, b: EmployeeSummary, key: SortKey, dir: SortDir): number {
  let cmp = 0;
  switch (key) {
    case 'name':
      cmp = a.name.localeCompare(b.name, 'ja');
      break;
    case 'phoneNumber':
      cmp = a.phoneNumber.localeCompare(b.phoneNumber);
      break;
    case 'isAdmin':
      cmp = (a.isAdmin ? 1 : 0) - (b.isAdmin ? 1 : 0);
      break;
    case 'status':
      cmp = ((a.deleted ?? false) ? 1 : 0) - ((b.deleted ?? false) ? 1 : 0);
      break;
  }
  return dir === 'asc' ? cmp : -cmp;
}

function sortIndicator(current: SortKey, target: SortKey, dir: SortDir): string {
  if (current !== target) return '';
  return dir === 'asc' ? ' ▲' : ' ▼';
}

export function EmployeeListPage({ client }: EmployeeListPageProps = {}): JSX.Element {
  const employeeClient = useMemo(() => client ?? new EmployeeClient(), [client]);

  const [employees, setEmployees] = useState<readonly EmployeeSummary[]>([]);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const sortedEmployees = useMemo(() => {
    return [...employees].sort((a, b) => compareEmployees(a, b, sortKey, sortDir));
  }, [employees, sortKey, sortDir]);

  const toggleSort = useCallback((key: SortKey) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortDir('asc');
      }
      return key;
    });
  }, []);

  const loadEmployees = useCallback(
    (withDeleted: boolean) => {
      setLoading(true);
      setErrorMessage(null);
      void (async () => {
        try {
          const list = await employeeClient.list({ includeDeleted: withDeleted });
          setEmployees(list);
        } catch (err) {
          if (err instanceof EmployeeApiError) {
            setErrorMessage(
              `一覧取得に失敗しました（HTTP ${err.status.toString()}）: ${err.serverMessage}`,
            );
          } else if (err instanceof Error) {
            setErrorMessage(`一覧取得に失敗しました: ${err.message}`);
          } else {
            setErrorMessage('一覧取得に失敗しました。');
          }
        } finally {
          setLoading(false);
        }
      })();
    },
    [employeeClient],
  );

  useEffect(() => {
    loadEmployees(includeDeleted);
  }, [loadEmployees, includeDeleted]);

  const requestDelete = useCallback((target: EmployeeSummary) => {
    setConfirmTarget({
      employeeId: target.employeeId,
      name: target.name,
      action: 'delete',
    });
  }, []);

  const requestCognitoDelete = useCallback((target: EmployeeSummary) => {
    setConfirmTarget({
      employeeId: target.employeeId,
      name: target.name,
      action: 'cognitoDelete',
    });
  }, []);

  const cancelConfirm = useCallback(() => {
    setConfirmTarget(null);
  }, []);

  const performConfirm = useCallback(() => {
    if (confirmTarget === null) return;
    setSubmitting(true);
    void (async () => {
      try {
        if (confirmTarget.action === 'delete') {
          await employeeClient.remove(confirmTarget.employeeId);
        } else {
          await employeeClient.removeCognitoUser(confirmTarget.employeeId);
        }
        setConfirmTarget(null);
        loadEmployees(includeDeleted);
      } catch (err) {
        const actionLabel = confirmTarget.action === 'delete' ? '削除' : 'Cognito 削除';
        if (err instanceof EmployeeApiError) {
          setErrorMessage(
            `${actionLabel}に失敗しました（HTTP ${err.status.toString()}）: ${err.serverMessage}`,
          );
        } else if (err instanceof Error) {
          setErrorMessage(`${actionLabel}に失敗しました: ${err.message}`);
        } else {
          setErrorMessage(`${actionLabel}に失敗しました。`);
        }
        setConfirmTarget(null);
      } finally {
        setSubmitting(false);
      }
    })();
  }, [confirmTarget, employeeClient, includeDeleted, loadEmployees]);

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
        <h1>社員マスタ</h1>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <Link to="/employees/new">
            <button type="button">新規社員追加</button>
          </Link>
          <Link to="/employees/import">
            <button type="button">CSV インポート</button>
          </Link>
        </div>
      </header>

      <div style={{ marginBottom: '0.75rem' }}>
        <label>
          <input
            type="checkbox"
            checked={includeDeleted}
            onChange={(e) => {
              setIncludeDeleted(e.target.checked);
            }}
          />{' '}
          論理削除済社員も表示
        </label>
      </div>

      {errorMessage !== null && (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {errorMessage}
        </p>
      )}

      {loading ? (
        <p role="status" aria-live="polite">
          読み込み中…
        </p>
      ) : employees.length === 0 ? (
        <p>社員レコードはまだ登録されていません。</p>
      ) : (
        <figure>
        <table>
          <thead>
            <tr>
              <th style={{ cursor: 'pointer' }} onClick={() => { toggleSort('name'); }}>氏名{sortIndicator(sortKey, 'name', sortDir)}</th>
              <th style={{ cursor: 'pointer' }} onClick={() => { toggleSort('phoneNumber'); }}>電話番号{sortIndicator(sortKey, 'phoneNumber', sortDir)}</th>
              <th style={{ cursor: 'pointer' }} onClick={() => { toggleSort('isAdmin'); }}>管理者{sortIndicator(sortKey, 'isAdmin', sortDir)}</th>
              <th style={{ cursor: 'pointer' }} onClick={() => { toggleSort('status'); }}>状態{sortIndicator(sortKey, 'status', sortDir)}</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {sortedEmployees.map((emp) => {
              const isDeleted = emp.deleted === true;
              const showCognitoDelete = isDeleted && emp.isAdmin;
              return (
                <tr key={emp.employeeId}>
                  <td>{emp.name}</td>
                  <td>{e164ToDomestic(emp.phoneNumber)}</td>
                  <td>{emp.isAdmin ? '○' : ''}</td>
                  <td>{isDeleted ? '削除済' : 'アクティブ'}</td>
                  <td>
                    {!isDeleted && (
                      <>
                        <Link to={`/employees/${encodeURIComponent(emp.employeeId)}/edit`} aria-label={`${emp.name}を編集`}>
                          <button type="button" className="btn-icon" aria-label="編集">✏️</button>
                        </Link>
                        <button
                          type="button"
                          className="btn-icon-danger"
                          aria-label={`${emp.name}を削除`}
                          onClick={() => {
                            requestDelete(emp);
                          }}
                        >
                          🗑️
                        </button>
                      </>
                    )}
                    {showCognitoDelete && (
                      <button
                        type="button"
                        className="btn-icon-danger"
                        aria-label={`${emp.name}のCognitoアカウントを削除`}
                        onClick={() => {
                          requestCognitoDelete(emp);
                        }}
                      >
                        🔐
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </figure>
      )}

      {confirmTarget !== null && (
        <dialog open role="dialog" aria-modal="true" aria-labelledby="employee-confirm-title">
          <article>
            {confirmTarget.action === 'delete' ? (
              <>
                <h2 id="employee-confirm-title">削除の確認</h2>
                <p>
                  社員「<strong>{confirmTarget.name}</strong>」を論理削除します。
                  <br />
                  削除すると、本社員はサイクル対象から除外され、Inbound 着信時の照合からも外れます。
                </p>
              </>
            ) : (
              <>
                <h2 id="employee-confirm-title">Cognito アカウント削除の確認</h2>
                <p>
                  社員「<strong>{confirmTarget.name}</strong>」の Cognito
                  アカウントを完全削除します。
                  <br />
                  <strong style={{ color: '#b91c1c' }}>この操作は元に戻せません。</strong>
                  削除後、本社員は SPA にログインできなくなります。
                </p>
              </>
            )}
            <footer style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button type="button" className="secondary" onClick={cancelConfirm} disabled={submitting}>
                キャンセル
              </button>
              <button
                type="button"
                onClick={performConfirm}
                disabled={submitting}
                className="contrast"
              >
                {submitting
                  ? '実行中…'
                  : confirmTarget.action === 'delete'
                    ? '削除する'
                    : 'Cognito 削除する'}
              </button>
            </footer>
          </article>
        </dialog>
      )}
    </section>
  );
}
