/**
 * 社員マスタ API クライアント。
 *
 * バックエンド `backend/lambdas/employee_api/handler.py` の以下ルートを叩く：
 *   - POST   /employees           : 社員追加
 *   - GET    /employees           : 社員一覧
 *   - GET    /employees/{id}      : 社員参照
 *   - PUT    /employees/{id}      : 社員更新
 *   - DELETE /employees/{id}      : 社員論理削除
 *   - POST   /employees/import    : CSV インポート（base64）
 *
 * 設計判断：
 *   - `createAuthFetch` を経由して Authorization ヘッダを付与する。
 *     未認証 / セッション失効は `sessionExpiredEvent` が再ログイン誘導するため、
 *     本層は HTTP ステータスを翻訳することに専念する。
 *   - 19原則(b)：エラーはエラーのまま返す。HTTP エラーは `EmployeeApiError`
 *     として throw し、ステータスコード・サーバーからのエラーメッセージを保持する。
 *   - 戻り型は backend handler の JSON 出力スキーマと厳密に一致する（DRY 原則：
 *     仕様は backend handler を正とし、本層はそれをそのまま受ける）。
 */

import { createAuthFetch, getApiBaseUrl, type AuthFetch } from './httpClient';

/** バックエンドの GET/POST レスポンスに含まれる単一社員の表現。 */
export interface EmployeeSummary {
  readonly employeeId: string;
  readonly name: string;
  readonly phoneNumber: string;
  readonly isAdmin: boolean;
  /**
   * 論理削除済フラグ（Task 15.16）。
   *
   * バックエンド `GET /employees?includeDeleted=true` 経由で取得した
   * レスポンスには `deleted: boolean` が含まれる。`?includeDeleted` 未指定
   * の従来呼出ではバックエンドが `deleted` フィールドを返さない可能性が
   * あるため、SPA 側はデフォルト false で扱える Optional とする。
   */
  readonly deleted?: boolean;
}

/** GET /employees/{id} のレスポンスは createdAt/updatedAt も含む拡張版。 */
export interface EmployeeDetail extends EmployeeSummary {
  readonly createdAt?: string;
  readonly updatedAt?: string;
}

/** POST /employees のリクエストボディ。 */
export interface CreateEmployeePayload {
  readonly name: string;
  readonly phoneNumber: string;
  /**
   * 管理者権限を付与するかどうか（Requirement 2.1 改訂）。
   * 未指定は false 相当（バックエンド側で `bool(body.get("isAdmin", False))`）。
   * true の場合は `adminEmail` が必須で、バックエンドは
   * `admin_create_user` を先に呼んで Cognito 管理者ユーザーを作成する。
   */
  readonly isAdmin?: boolean;
  /**
   * 管理者権限付与時（`isAdmin=true`）の Cognito ログイン用 email。
   * `isAdmin=false` の場合は送信しなくてよい。
   * 形式検証は `isValidEmail`（`validation.ts`）で事前に行う。
   */
  readonly adminEmail?: string;
}

/** PUT /employees/{id} のリクエストボディ（部分更新ではなく全項目送信）。 */
export interface UpdateEmployeePayload {
  readonly name: string;
  readonly phoneNumber: string;
}

/** POST /employees/import の成功レスポンスと、行レベル失敗レポートを統合した型。 */
export interface ImportCsvResult {
  readonly imported: number;
  readonly attempted: number;
  readonly errors: readonly ImportCsvError[];
}

export interface ImportCsvError {
  readonly line?: number;
  readonly reason: string;
}

/** HTTP エラー詳細を保持する例外。UI 層がメッセージ翻訳に使う。 */
export class EmployeeApiError extends Error {
  readonly status: number;
  readonly serverMessage: string;
  /** バックエンドのインポート用 errors 配列があれば保持する（403/409/500 等）。 */
  readonly importErrors?: readonly ImportCsvError[];

  constructor(status: number, serverMessage: string, importErrors?: readonly ImportCsvError[]) {
    super(`HTTP ${status.toString()}: ${serverMessage}`);
    this.name = 'EmployeeApiError';
    this.status = status;
    this.serverMessage = serverMessage;
    if (importErrors !== undefined) {
      this.importErrors = importErrors;
    }
  }
}

export interface EmployeeClientOptions {
  /** テスト DI：認証付き fetch。未指定なら `createAuthFetch()` を使う。 */
  readonly authFetch?: AuthFetch;
  /** テスト DI：API ベース URL。未指定なら `getApiBaseUrl()` を使う。 */
  readonly baseUrl?: string;
}

/**
 * 社員 API を集約したクライアント。
 *
 * シングルトンは作らず、呼出側で生成する（テストでフェイク差替を容易にするため）。
 */
export class EmployeeClient {
  private readonly fetchImpl: AuthFetch;
  private readonly baseUrl: string;

  constructor(options: EmployeeClientOptions = {}) {
    this.fetchImpl = options.authFetch ?? createAuthFetch();
    this.baseUrl = options.baseUrl ?? getApiBaseUrl();
  }

  async list(options: { includeDeleted?: boolean } = {}): Promise<readonly EmployeeSummary[]> {
    const query = options.includeDeleted === true ? '?includeDeleted=true' : '';
    const res = await this.fetchImpl(`${this.baseUrl}/employees${query}`, { method: 'GET' });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new EmployeeApiError(res.status, extractServerMessage(body));
    }
    return readEmployeesArray(body);
  }

  async get(employeeId: string): Promise<EmployeeDetail> {
    const res = await this.fetchImpl(
      `${this.baseUrl}/employees/${encodeURIComponent(employeeId)}`,
      { method: 'GET' },
    );
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new EmployeeApiError(res.status, extractServerMessage(body));
    }
    return readEmployeeDetail(body);
  }

  async create(payload: CreateEmployeePayload): Promise<EmployeeSummary> {
    const res = await this.fetchImpl(`${this.baseUrl}/employees`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new EmployeeApiError(res.status, extractServerMessage(body));
    }
    return readEmployeeSummary(body);
  }

  async update(employeeId: string, payload: UpdateEmployeePayload): Promise<EmployeeSummary> {
    const res = await this.fetchImpl(
      `${this.baseUrl}/employees/${encodeURIComponent(employeeId)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    );
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new EmployeeApiError(res.status, extractServerMessage(body));
    }
    return readEmployeeSummary(body);
  }

  async remove(employeeId: string): Promise<void> {
    const res = await this.fetchImpl(
      `${this.baseUrl}/employees/${encodeURIComponent(employeeId)}`,
      { method: 'DELETE' },
    );
    if (!res.ok) {
      const body = await parseJsonBody(res);
      throw new EmployeeApiError(res.status, extractServerMessage(body));
    }
    // 200 + JSON body { employeeId, deleted: true } は読み捨て可能。
  }

  /**
   * Cognito アカウント削除（Task 15.16）。
   *
   * 退職時の管理者アカウント削除を SPA から実行可能にする。
   * 前提：対象社員は論理削除済（`deleted=true`）かつ Cognito ユーザー紐付け
   * （`cognitoSub`）を持つ管理者ロール社員。API 側でこれらを検証し、
   * 違反時は 409 / 404 を返す。SPA 側でも UI 上でこれらの条件を満たす
   * 行にのみボタンを表示することで、誤操作を二段階で防ぐ。
   *
   * 不可逆操作：Cognito User Pool には Soft Delete 機能が無いため、
   * 削除すると元に戻せない。SPA は確認ダイアログでその旨を明示する。
   */
  async removeCognitoUser(employeeId: string): Promise<void> {
    const res = await this.fetchImpl(
      `${this.baseUrl}/employees/${encodeURIComponent(employeeId)}/cognito-user`,
      { method: 'DELETE' },
    );
    if (!res.ok) {
      const body = await parseJsonBody(res);
      throw new EmployeeApiError(res.status, extractServerMessage(body));
    }
    // 200 + { employeeId, cognitoUserDeleted: true } は読み捨て可能。
  }

  async importCsv(csvBase64: string): Promise<ImportCsvResult> {
    const res = await this.fetchImpl(`${this.baseUrl}/employees/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ csvBase64 }),
    });
    const body = await parseJsonBody(res);
    const parsed = readImportCsvBody(body);
    if (!res.ok) {
      throw new EmployeeApiError(res.status, extractServerMessage(body), parsed.errors);
    }
    return parsed;
  }
}

// ---------- 内部ヘルパ ----------

async function parseJsonBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (text === '') return {};
  try {
    return JSON.parse(text) as unknown;
  } catch {
    // バックエンドが常に JSON を返す前提のため、SyntaxError が出たら状態異常。
    // 19原則(b)：抑え込まず、上位でステータス込みで翻訳できるよう本文文字列を伝える。
    return { error: text };
  }
}

function extractServerMessage(body: unknown): string {
  if (body !== null && typeof body === 'object' && 'error' in body) {
    const value = (body as Record<string, unknown>).error;
    if (typeof value === 'string') return value;
  }
  return 'Unknown server error';
}

function readEmployeesArray(body: unknown): readonly EmployeeSummary[] {
  if (body === null || typeof body !== 'object') {
    throw new EmployeeApiError(0, 'Unexpected response shape (root not object)');
  }
  const obj = body as Record<string, unknown>;
  const raw = obj.employees;
  if (!Array.isArray(raw)) {
    throw new EmployeeApiError(0, 'Unexpected response shape (employees not array)');
  }
  return raw.map((item, idx) => {
    if (item === null || typeof item !== 'object') {
      throw new EmployeeApiError(0, `Unexpected employee entry at index ${idx.toString()}`);
    }
    return readEmployeeSummary(item);
  });
}

function readEmployeeSummary(body: unknown): EmployeeSummary {
  if (body === null || typeof body !== 'object') {
    throw new EmployeeApiError(0, 'Unexpected response shape (employee not object)');
  }
  const obj = body as Record<string, unknown>;
  const employeeId = obj.employeeId;
  const name = obj.name;
  const phoneNumber = obj.phoneNumber;
  const isAdmin = obj.isAdmin;
  const deleted = obj.deleted;
  if (
    typeof employeeId !== 'string' ||
    typeof name !== 'string' ||
    typeof phoneNumber !== 'string'
  ) {
    throw new EmployeeApiError(0, 'Unexpected response shape (employee fields)');
  }
  return {
    employeeId,
    name,
    phoneNumber,
    isAdmin: typeof isAdmin === 'boolean' ? isAdmin : false,
    ...(typeof deleted === 'boolean' ? { deleted } : {}),
  };
}

function readEmployeeDetail(body: unknown): EmployeeDetail {
  const summary = readEmployeeSummary(body);
  const obj = body as Record<string, unknown>;
  const createdAt = obj.createdAt;
  const updatedAt = obj.updatedAt;
  const detail: EmployeeDetail = {
    ...summary,
    ...(typeof createdAt === 'string' ? { createdAt } : {}),
    ...(typeof updatedAt === 'string' ? { updatedAt } : {}),
  };
  return detail;
}

function readImportCsvBody(body: unknown): ImportCsvResult {
  if (body === null || typeof body !== 'object') {
    return { imported: 0, attempted: 0, errors: [] };
  }
  const obj = body as Record<string, unknown>;
  const imported = typeof obj.imported === 'number' ? obj.imported : 0;
  const attempted = typeof obj.attempted === 'number' ? obj.attempted : 0;
  const errorsRaw = obj.errors;
  const errors: ImportCsvError[] = [];
  if (Array.isArray(errorsRaw)) {
    for (const entry of errorsRaw as unknown[]) {
      if (entry === null || typeof entry !== 'object') continue;
      const e = entry as Record<string, unknown>;
      const reason = typeof e.reason === 'string' ? e.reason : '';
      const line = typeof e.line === 'number' ? e.line : undefined;
      errors.push(line === undefined ? { reason } : { line, reason });
    }
  }
  return { imported, attempted, errors };
}
