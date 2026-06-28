/**
 * キーワード辞書 API クライアント（Phase 10.9、Requirement 8.1〜8.4 / 8.7）。
 *
 * バックエンド `backend/lambdas/dictionary_api/handler.py` の以下ルートに対応する：
 *   - GET    /keyword-dictionary                          -> list_all
 *   - GET    /keyword-dictionary/version                  -> get_current_version
 *   - POST   /keyword-dictionary                          -> create_keyword
 *   - PATCH  /keyword-dictionary/{category}/{keyword}     -> update_keyword
 *   - DELETE /keyword-dictionary/{category}/{keyword}     -> delete_keyword
 *
 * 設計判断：
 *   - `createAuthFetch` 経由で Authorization ヘッダを付与する（既存
 *     `InboundClient` / `RecordingClient` と同じ DI 構造）。
 *   - 19原則(b)：HTTP エラーは握り潰さず `DictionaryApiError` として throw。
 *     409 Conflict は楽観ロック競合のため、専用例外 `DictionaryConflictError`
 *     として区別可能にする（UI 側で自動再取得 + バナー表示するため）。
 *   - バックエンドは `GET /keyword-dictionary` で version を含まず
 *     カテゴリ別辞書だけを返す設計のため、`list()` メソッドでは
 *     `GET /keyword-dictionary` と `GET /keyword-dictionary/version` を
 *     **並列発火**して `DictionarySnapshot` に合成する。
 *   - PATCH は backend 側で「version stamp only」（同じ keyword を残し
 *     version 番号だけ進める）動作。design.md ADR ズレは ADR-0006 で
 *     記録する。UI 上は「touch（バージョン更新）」として提示する。
 *   - 409 レスポンス body は `{ "error": "Concurrent modification..." }` で
 *     `latestVersion` フィールドを含まないため、`DictionaryConflictError`
 *     の `latestVersion` は常に null。将来バックエンド拡張で同梱できる
 *     ようにフィールドだけ確保する。
 *   - 19原則(a) DRY：`parseJsonBody` / `extractServerMessage` /
 *     shape チェックヘルパは `inboundClient` / `recordingClient` と
 *     同型で独立実装し、命名を揃えて将来の共通化に備える。
 */

import { createAuthFetch, getApiBaseUrl, type AuthFetch } from './httpClient';

/** 辞書のカテゴリ 3 値（design.md D7、backend `VALID_CATEGORIES` と整合）。 */
export type DictionaryCategory = 'SAFE' | 'INJURED' | 'UNAVAILABLE';

export const VALID_CATEGORIES: readonly DictionaryCategory[] = ['SAFE', 'INJURED', 'UNAVAILABLE'];

function isDictionaryCategory(v: unknown): v is DictionaryCategory {
  return typeof v === 'string' && (VALID_CATEGORIES as readonly string[]).includes(v);
}

/**
 * 辞書の現在状態スナップショット。
 *
 * `categories` は 3 カテゴリ全てを必ず含む（空配列でも空キーは作らず空配列を入れる）。
 * `version` は最新の楽観ロックバージョン番号（META.currentVersion）。
 */
export interface DictionarySnapshot {
  readonly categories: Readonly<Record<DictionaryCategory, readonly string[]>>;
  readonly version: number;
}

/** HTTP エラー詳細を保持する例外。UI 層がメッセージ翻訳に使う。 */
export class DictionaryApiError extends Error {
  readonly status: number;
  readonly serverMessage: string;

  constructor(status: number, serverMessage: string) {
    super(`HTTP ${status.toString()}: ${serverMessage}`);
    this.name = 'DictionaryApiError';
    this.status = status;
    this.serverMessage = serverMessage;
  }
}

/**
 * 楽観ロック競合専用の例外（HTTP 409）。
 *
 * バックエンドの現実装では body に `latestVersion` を含めない設計のため、
 * `latestVersion` は常に null となる。将来バックエンド拡張で同梱できる
 * ようにフィールドだけ確保する（UI 側は本フィールドが null でも動作可能）。
 */
export class DictionaryConflictError extends DictionaryApiError {
  readonly latestVersion: number | null;

  constructor(serverMessage: string, latestVersion: number | null) {
    super(409, serverMessage);
    this.name = 'DictionaryConflictError';
    this.latestVersion = latestVersion;
  }
}

export interface DictionaryClientOptions {
  /** テスト DI：認証付き fetch。未指定なら `createAuthFetch()`。 */
  readonly authFetch?: AuthFetch;
  /** テスト DI：API ベース URL。未指定なら `getApiBaseUrl()`。 */
  readonly baseUrl?: string;
}

/** mutation API が返す共通形（POST / PATCH / DELETE）。 */
interface MutationResult {
  readonly category: DictionaryCategory;
  readonly keyword: string;
  readonly version: number;
}

/**
 * キーワード辞書 API を集約したクライアント。
 *
 * シングルトンは作らず、呼出側で生成する（テストでフェイク差替を容易にするため）。
 */
export class DictionaryClient {
  private readonly fetchImpl: AuthFetch;
  private readonly baseUrl: string;

  constructor(options: DictionaryClientOptions = {}) {
    this.fetchImpl = options.authFetch ?? createAuthFetch();
    this.baseUrl = options.baseUrl ?? getApiBaseUrl();
  }

  /**
   * 辞書全体と現在バージョンを取得する（並列発火）。
   *
   * 内部で `GET /keyword-dictionary` と `GET /keyword-dictionary/version` を
   * 並列に呼び出し、`DictionarySnapshot` に合成して返す。
   */
  async list(): Promise<DictionarySnapshot> {
    const [groupedRes, versionRes] = await Promise.all([
      this.fetchImpl(`${this.baseUrl}/keyword-dictionary`, { method: 'GET' }),
      this.fetchImpl(`${this.baseUrl}/keyword-dictionary/version`, { method: 'GET' }),
    ]);
    const groupedBody = await parseJsonBody(groupedRes);
    const versionBody = await parseJsonBody(versionRes);
    if (!groupedRes.ok) {
      throw buildApiError(groupedRes.status, groupedBody);
    }
    if (!versionRes.ok) {
      throw buildApiError(versionRes.status, versionBody);
    }
    const categories = readDictionaryGrouped(groupedBody);
    const version = readVersion(versionBody);
    return { categories, version };
  }

  /** 現在の辞書バージョン番号のみを取得する。 */
  async getVersion(): Promise<{ readonly version: number }> {
    const res = await this.fetchImpl(`${this.baseUrl}/keyword-dictionary/version`, {
      method: 'GET',
    });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw buildApiError(res.status, body);
    }
    return { version: readVersion(body) };
  }

  /**
   * キーワードを追加する（POST /keyword-dictionary）。
   *
   * @param category 追加先カテゴリ。
   * @param keyword 追加するキーワード（非空文字列）。
   * @param expectedVersion 楽観ロックの期待バージョン（list() で取得した最新値）。
   * @returns 採番された新バージョン番号。
   */
  async add(
    category: DictionaryCategory,
    keyword: string,
    expectedVersion: number,
  ): Promise<MutationResult> {
    return this.mutate('POST', `${this.baseUrl}/keyword-dictionary`, {
      category,
      keyword,
      expectedVersion,
    });
  }

  /**
   * キーワードを削除する（DELETE /keyword-dictionary/{category}/{keyword}）。
   *
   * @returns 採番された新バージョン番号。
   */
  async remove(
    category: DictionaryCategory,
    keyword: string,
    expectedVersion: number,
  ): Promise<MutationResult> {
    const url = `${this.baseUrl}/keyword-dictionary/${encodeURIComponent(category)}/${encodeURIComponent(keyword)}`;
    return this.mutate('DELETE', url, { expectedVersion });
  }

  /**
   * キーワードを touch する（PATCH /keyword-dictionary/{category}/{keyword}）。
   *
   * backend の現実装では「version stamp only」動作（同じ keyword を残し
   * version 番号だけ進める）。design.md の意図（PATCH = 有効フラグ更新）との
   * ズレは ADR-0006 に記録。UI 上は「バージョン更新（touch）」として提示する。
   *
   * @returns 採番された新バージョン番号。
   */
  async touch(
    category: DictionaryCategory,
    keyword: string,
    expectedVersion: number,
  ): Promise<MutationResult> {
    const url = `${this.baseUrl}/keyword-dictionary/${encodeURIComponent(category)}/${encodeURIComponent(keyword)}`;
    return this.mutate('PATCH', url, { expectedVersion });
  }

  private async mutate(
    method: 'POST' | 'PATCH' | 'DELETE',
    url: string,
    body: Record<string, unknown>,
  ): Promise<MutationResult> {
    const res = await this.fetchImpl(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const resBody = await parseJsonBody(res);
    if (!res.ok) {
      throw buildApiError(res.status, resBody);
    }
    return readMutationResult(resBody);
  }
}

// ---------- 内部ヘルパ ----------

async function parseJsonBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (text === '') return {};
  try {
    return JSON.parse(text) as unknown;
  } catch {
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

/**
 * `status` と JSON body から適切な例外を生成する。
 *
 * 409 は `DictionaryConflictError` を、それ以外は `DictionaryApiError`
 * を返す。バックエンドが将来 `latestVersion` を同梱した場合は受領可能。
 */
function buildApiError(status: number, body: unknown): DictionaryApiError {
  const serverMessage = extractServerMessage(body);
  if (status === 409) {
    let latestVersion: number | null = null;
    if (body !== null && typeof body === 'object') {
      const v = (body as Record<string, unknown>).latestVersion;
      if (typeof v === 'number' && Number.isFinite(v)) {
        latestVersion = v;
      }
    }
    return new DictionaryConflictError(serverMessage, latestVersion);
  }
  return new DictionaryApiError(status, serverMessage);
}

/**
 * `GET /keyword-dictionary` の応答 body を `DictionarySnapshot.categories`
 * の形に正規化する。
 *
 * バックエンド契約：`{ SAFE: string[], INJURED: string[], UNAVAILABLE: string[] }`。
 * いずれかのカテゴリが配列でない場合は shape エラーとして throw。
 * 配列内の文字列以外要素は除外する（防御的）。
 * 既知 3 カテゴリ以外のキーは無視する。
 */
function readDictionaryGrouped(
  body: unknown,
): Readonly<Record<DictionaryCategory, readonly string[]>> {
  if (body === null || typeof body !== 'object') {
    throw new DictionaryApiError(0, 'Unexpected response shape (root not object)');
  }
  const obj = body as Record<string, unknown>;
  const result: Record<DictionaryCategory, readonly string[]> = {
    SAFE: [],
    INJURED: [],
    UNAVAILABLE: [],
  };
  for (const category of VALID_CATEGORIES) {
    const raw = obj[category];
    if (raw === undefined) {
      // category キー自体が無い場合は空配列扱い
      continue;
    }
    if (!Array.isArray(raw)) {
      throw new DictionaryApiError(0, `Unexpected response shape (${category} is not an array)`);
    }
    result[category] = raw.filter((v): v is string => typeof v === 'string');
  }
  // category キー検証：未知カテゴリは無視するだけで良い（型安全のため）
  for (const key of Object.keys(obj)) {
    if (!isDictionaryCategory(key)) {
      // 例外を出さず無視（将来カテゴリ追加時の前方互換性）
      continue;
    }
  }
  return result;
}

function readVersion(body: unknown): number {
  if (body === null || typeof body !== 'object') {
    throw new DictionaryApiError(0, 'Unexpected response shape (version body not object)');
  }
  const v = (body as Record<string, unknown>).version;
  if (typeof v !== 'number' || !Number.isFinite(v)) {
    throw new DictionaryApiError(0, 'Unexpected response shape (version is not a finite number)');
  }
  return v;
}

function readMutationResult(body: unknown): MutationResult {
  if (body === null || typeof body !== 'object') {
    throw new DictionaryApiError(0, 'Unexpected response shape (mutation body not object)');
  }
  const obj = body as Record<string, unknown>;
  const category = obj.category;
  const keyword = obj.keyword;
  const version = obj.version;
  if (!isDictionaryCategory(category)) {
    throw new DictionaryApiError(0, 'Unexpected response shape (mutation category invalid)');
  }
  if (typeof keyword !== 'string' || keyword === '') {
    throw new DictionaryApiError(0, 'Unexpected response shape (mutation keyword invalid)');
  }
  if (typeof version !== 'number' || !Number.isFinite(version)) {
    throw new DictionaryApiError(0, 'Unexpected response shape (mutation version invalid)');
  }
  return { category, keyword, version };
}
