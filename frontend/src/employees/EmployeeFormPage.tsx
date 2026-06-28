/**
 * 社員追加 / 編集フォーム（管理者画面 / Phase 10.4）。
 *
 * 対応要件：
 *   - Requirement 2.1 : 入力項目は氏名と電話番号のみ。
 *   - Requirement 2.2 : 新規追加成功時に社員 ID（UUID）採番（バックエンド側）。
 *   - Requirement 2.7 : E.164 形式違反は API へ送らず即時エラー表示。
 *
 * 設計判断：
 *   - 1 つのコンポーネントで「新規追加（`/employees/new`）」と
 *     「既存編集（`/employees/:id/edit`）」を兼ねる。URL からモードを判定し、
 *     編集モードでは初期値を `GET /employees/{id}` で取得する。
 *   - 送信前に `isValidName` / `isValidE164` を SPA 側で必ず検査し、
 *     違反があれば API を呼ばずに UI 上でフィールド別エラーを表示する
 *     （バックエンド `validate.py` と同じ仕様を SPA に保つ）。
 *   - サーバー側エラー（409 重複、400 等）は `EmployeeApiError.serverMessage`
 *     を翻訳せずそのまま表示。19原則(b)。
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent, type JSX } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { EmployeeApiError, EmployeeClient } from '../api/employeeClient';

import { MAX_NAME_LENGTH, isValidE164, isValidName } from './validation';

export interface EmployeeFormPageProps {
  /** テスト DI：未指定なら `new EmployeeClient()`。 */
  readonly client?: EmployeeClient;
}

interface FieldErrors {
  readonly name?: string;
  readonly phoneNumber?: string;
}

export function EmployeeFormPage({ client }: EmployeeFormPageProps = {}): JSX.Element {
  const params = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const employeeClient = useMemo(() => client ?? new EmployeeClient(), [client]);

  const editTarget: string | null =
    typeof params.id === 'string' && params.id !== '' ? params.id : null;
  const isEditMode = editTarget !== null;

  const [name, setName] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [loadingInitial, setLoadingInitial] = useState(isEditMode);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  useEffect(() => {
    if (editTarget === null) return;
    setLoadingInitial(true);
    void (async () => {
      try {
        const detail = await employeeClient.get(editTarget);
        setName(detail.name);
        setPhoneNumber(detail.phoneNumber);
      } catch (err) {
        if (err instanceof EmployeeApiError) {
          setServerError(`取得失敗（HTTP ${err.status.toString()}）: ${err.serverMessage}`);
        } else if (err instanceof Error) {
          setServerError(`取得失敗: ${err.message}`);
        } else {
          setServerError('取得失敗。');
        }
      } finally {
        setLoadingInitial(false);
      }
    })();
  }, [editTarget, employeeClient]);

  const validate = useCallback((): FieldErrors => {
    const errs: { name?: string; phoneNumber?: string } = {};
    if (!isValidName(name)) {
      errs.name = `氏名は 1〜${MAX_NAME_LENGTH.toString()} 文字で入力してください。`;
    }
    if (!isValidE164(phoneNumber)) {
      errs.phoneNumber =
        '電話番号は E.164 形式（先頭 + に続けて 1〜15 桁の数字）で入力してください。';
    }
    return errs;
  }, [name, phoneNumber]);

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setServerError(null);
      const errs = validate();
      setFieldErrors(errs);
      if (Object.keys(errs).length > 0) return;

      setSubmitting(true);
      void (async () => {
        try {
          if (editTarget !== null) {
            await employeeClient.update(editTarget, { name, phoneNumber });
          } else {
            await employeeClient.create({ name, phoneNumber });
          }
          navigate('/employees', { replace: true });
        } catch (err) {
          if (err instanceof EmployeeApiError) {
            setServerError(`保存失敗（HTTP ${err.status.toString()}）: ${err.serverMessage}`);
          } else if (err instanceof Error) {
            setServerError(`保存失敗: ${err.message}`);
          } else {
            setServerError('保存失敗。');
          }
        } finally {
          setSubmitting(false);
        }
      })();
    },
    [validate, editTarget, employeeClient, name, phoneNumber, navigate],
  );

  const onCancel = useCallback(() => {
    navigate('/employees');
  }, [navigate]);

  if (loadingInitial) {
    return (
      <p role="status" aria-live="polite">
        読み込み中…
      </p>
    );
  }

  return (
    <section>
      <h1>{isEditMode ? '社員の編集' : '社員の追加'}</h1>

      {serverError !== null && (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {serverError}
        </p>
      )}

      <form onSubmit={onSubmit} noValidate style={{ maxWidth: '480px' }}>
        <div style={{ marginBottom: '1rem' }}>
          <label htmlFor="employee-name" style={{ display: 'block', marginBottom: '0.25rem' }}>
            氏名（必須）
          </label>
          <input
            id="employee-name"
            name="name"
            type="text"
            required
            value={name}
            maxLength={MAX_NAME_LENGTH}
            onChange={(e) => {
              setName(e.target.value);
            }}
            style={{ width: '100%', padding: '0.5rem' }}
          />
          {fieldErrors.name !== undefined && (
            <p role="alert" style={{ color: '#b91c1c', marginTop: '0.25rem' }}>
              {fieldErrors.name}
            </p>
          )}
        </div>

        <div style={{ marginBottom: '1rem' }}>
          <label htmlFor="employee-phone" style={{ display: 'block', marginBottom: '0.25rem' }}>
            電話番号（E.164 形式、例：+819012345678）
          </label>
          <input
            id="employee-phone"
            name="phoneNumber"
            type="tel"
            required
            value={phoneNumber}
            onChange={(e) => {
              setPhoneNumber(e.target.value);
            }}
            placeholder="+819012345678"
            style={{ width: '100%', padding: '0.5rem' }}
          />
          {fieldErrors.phoneNumber !== undefined && (
            <p role="alert" style={{ color: '#b91c1c', marginTop: '0.25rem' }}>
              {fieldErrors.phoneNumber}
            </p>
          )}
        </div>

        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button type="submit" disabled={submitting} style={{ padding: '0.5rem 1rem' }}>
            {submitting ? '保存中…' : isEditMode ? '更新する' : '追加する'}
          </button>
          <button type="button" onClick={onCancel} disabled={submitting}>
            キャンセル
          </button>
        </div>
      </form>
    </section>
  );
}
