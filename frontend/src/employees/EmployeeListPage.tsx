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

export function EmployeeListPage({ client }: EmployeeListPageProps = {}): JSX.Element {
  const employeeClient = useMemo(() => client ?? new EmployeeClient(), [client]);

  const [employees, setEmployees] = useState<readonly EmployeeSummary[]>([]);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={cellStyle}>氏名</th>
              <th style={cellStyle}>電話番号</th>
              <th style={cellStyle}>管理者</th>
              <th style={cellStyle}>状態</th>
              <th style={cellStyle}>操作</th>
            </tr>
          </thead>
          <tbody>
            {employees.map((emp) => {
              const isDeleted = emp.deleted === true;
              const showCognitoDelete = isDeleted && emp.isAdmin;
              return (
                <tr key={emp.employeeId}>
                  <td style={cellStyle}>{emp.name}</td>
                  <td style={cellStyle}>{emp.phoneNumber}</td>
                  <td style={cellStyle}>{emp.isAdmin ? '○' : ''}</td>
                  <td style={cellStyle}>{isDeleted ? '削除済' : 'アクティブ'}</td>
                  <td style={cellStyle}>
                    {!isDeleted && (
                      <>
                        <Link to={`/employees/${encodeURIComponent(emp.employeeId)}/edit`}>
                          <button type="button">編集</button>
                        </Link>{' '}
                        <button
                          type="button"
                          onClick={() => {
                            requestDelete(emp);
                          }}
                        >
                          削除
                        </button>
                      </>
                    )}
                    {showCognitoDelete && (
                      <button
                        type="button"
                        onClick={() => {
                          requestCognitoDelete(emp);
                        }}
                        style={{ background: '#b91c1c', color: '#fff' }}
                      >
                        Cognito 削除
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {confirmTarget !== null && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="employee-confirm-title"
          style={dialogStyle}
        >
          <div style={dialogInnerStyle}>
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
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button type="button" onClick={cancelConfirm} disabled={submitting}>
                キャンセル
              </button>
              <button
                type="button"
                onClick={performConfirm}
                disabled={submitting}
                style={{ background: '#b91c1c', color: '#fff' }}
              >
                {submitting
                  ? '実行中…'
                  : confirmTarget.action === 'delete'
                    ? '削除する'
                    : 'Cognito 削除する'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

const cellStyle: React.CSSProperties = {
  border: '1px solid #d1d5db',
  padding: '0.5rem',
  textAlign: 'left',
};

const dialogStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0,0,0,0.5)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 10,
};

const dialogInnerStyle: React.CSSProperties = {
  background: '#fff',
  borderRadius: '0.5rem',
  padding: '1.5rem',
  maxWidth: '480px',
  width: '90%',
};
