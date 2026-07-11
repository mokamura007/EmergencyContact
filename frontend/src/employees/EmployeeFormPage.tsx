/**
 * 社員追加 / 編集フォーム（管理者画面 / Phase 10.4、Req 2.1 改訂対応）。
 *
 * 対応要件：
 *   - Requirement 2.1 （改訂）：入力項目は「氏名」「電話番号」「管理者権限
 *     フラグ（任意、既定 false）」「管理者 email（isAdmin=true 時のみ必須、
 *     RFC 5322 simplified 形式）」。管理者権限フラグと email 欄は新規追加
 *     モードでのみ表示する（編集モードでは扱わない、スコープ外）。
 *   - Requirement 2.2 : 新規追加成功時に社員 ID（UUID）採番（バックエンド側）。
 *     isAdmin=true の場合、バックエンドが Cognito 管理者ユーザーを作成し、
 *     日本語の招待メール（一時パスワード付き）を本人に送信する。
 *   - Requirement 2.7 : E.164 形式違反は API へ送らず即時エラー表示。
 *
 * 設計判断：
 *   - 1 つのコンポーネントで「新規追加（`/employees/new`）」と
 *     「既存編集（`/employees/:id/edit`）」を兼ねる。URL からモードを判定し、
 *     編集モードでは初期値を `GET /employees/{id}` で取得する。
 *   - 送信前に `isValidName` / `isValidE164` / `isValidEmail` を SPA 側で
 *     必ず検査し、違反があれば API を呼ばずに UI 上でフィールド別エラーを
 *     表示する（バックエンド `validate.py` と同じ仕様を SPA に保つ）。
 *   - 管理者権限チェックボックス OFF の場合は payload に `isAdmin` /
 *     `adminEmail` を含めない（未指定 = バックエンド側 False）。
 *   - サーバー側エラー（409 重複、400 等）は `EmployeeApiError.serverMessage`
 *     を翻訳せずそのまま表示。19原則(b)。
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent, type JSX } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  EmployeeApiError,
  EmployeeClient,
  type CreateEmployeePayload,
} from '../api/employeeClient';

import {
  MAX_NAME_LENGTH,
  domesticToE164,
  e164ToDomestic,
  isValidDomesticPhone,
  isValidEmail,
  isValidName,
} from './validation';

export interface EmployeeFormPageProps {
  /** テスト DI：未指定なら `new EmployeeClient()`。 */
  readonly client?: EmployeeClient;
}

interface FieldErrors {
  readonly name?: string;
  readonly phoneNumber?: string;
  readonly adminEmail?: string;
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
  // 管理者権限フラグ / email は新規追加モードでのみ扱う（Req 2.1 改訂、
  // 編集モードでは非表示・非送信）。
  const [isAdmin, setIsAdmin] = useState(false);
  const [adminEmail, setAdminEmail] = useState('');
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
        setPhoneNumber(e164ToDomestic(detail.phoneNumber));
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
    const errs: { name?: string; phoneNumber?: string; adminEmail?: string } = {};
    if (!isValidName(name)) {
      errs.name = `氏名は 1〜${MAX_NAME_LENGTH.toString()} 文字で入力してください。`;
    }
    if (!isValidDomesticPhone(phoneNumber)) {
      errs.phoneNumber =
        '電話番号は国内形式（例：09012345678 または 0312345678）で入力してください。';
    }
    // 管理者権限チェックが ON の場合のみ email 形式を検査。編集モードでは
    // 管理者権限欄自体を描画しないため、ここに到達しても実質常に false。
    if (!isEditMode && isAdmin && !isValidEmail(adminEmail)) {
      errs.adminEmail =
        '管理者 email は有効なメール形式（例：user@example.com）で入力してください。';
    }
    return errs;
  }, [name, phoneNumber, isAdmin, adminEmail, isEditMode]);

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
          const e164Phone = domesticToE164(phoneNumber);
          if (editTarget !== null) {
            await employeeClient.update(editTarget, { name, phoneNumber: e164Phone });
          } else {
            // 管理者チェック OFF の場合は isAdmin / adminEmail を送らない
            // （payload に含めなければバックエンドは False として扱う）。
            // 電話番号は国内形式 (0XXXXXXXXXX) → E.164 (+81XXXXXXXXXX) に変換して送る。
            const payload: CreateEmployeePayload = isAdmin
              ? { name, phoneNumber: e164Phone, isAdmin: true, adminEmail }
              : { name, phoneNumber: e164Phone };
            await employeeClient.create(payload);
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
    [validate, editTarget, employeeClient, name, phoneNumber, isAdmin, adminEmail, navigate],
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
            電話番号（例：09012345678）
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
            placeholder="09012345678"
            style={{ width: '100%', padding: '0.5rem' }}
          />
          {fieldErrors.phoneNumber !== undefined && (
            <p role="alert" style={{ color: '#b91c1c', marginTop: '0.25rem' }}>
              {fieldErrors.phoneNumber}
            </p>
          )}
        </div>

        {/*
          管理者権限セクション。新規追加モード限定（Req 2.1 改訂、編集モード
          スコープ外）。チェック ON で email 欄を表示・必須化する。
        */}
        {!isEditMode && (
          <fieldset
            data-testid="admin-section"
            style={{
              marginBottom: '1rem',
              padding: '0.75rem',
              border: '1px solid #d1d5db',
              borderRadius: '4px',
            }}
          >
            <legend style={{ padding: '0 0.25rem' }}>管理者権限（任意）</legend>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <input
                type="checkbox"
                data-testid="admin-checkbox"
                checked={isAdmin}
                onChange={(e) => {
                  setIsAdmin(e.target.checked);
                }}
              />
              管理者権限を付与する（Cognito ログイン可能な管理者アカウントを作成）
            </label>
            {isAdmin && (
              <div style={{ marginTop: '0.75rem' }}>
                <label htmlFor="admin-email" style={{ display: 'block', marginBottom: '0.25rem' }}>
                  管理者 email（必須、招待メール送信先）
                </label>
                <input
                  id="admin-email"
                  data-testid="admin-email-input"
                  name="adminEmail"
                  type="email"
                  required
                  value={adminEmail}
                  onChange={(e) => {
                    setAdminEmail(e.target.value);
                  }}
                  placeholder="user@example.com"
                  style={{ width: '100%', padding: '0.5rem' }}
                />
                {fieldErrors.adminEmail !== undefined && (
                  <p role="alert" style={{ color: '#b91c1c', marginTop: '0.25rem' }}>
                    {fieldErrors.adminEmail}
                  </p>
                )}
                <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: '#4b5563' }}>
                  登録すると Cognito に管理者アカウントが作成され、
                  一時パスワード付きの招待メールが上記アドレスへ送信されます。
                  初回ログイン時にパスワード変更が求められます。
                </p>
              </div>
            )}
          </fieldset>
        )}

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
