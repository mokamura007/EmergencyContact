/**
 * インバウンド着信履歴 API クライアント（Phase 10.8、Requirement 13.7）。
 *
 * バックエンド `GET /inbound` に対応する。レスポンスは `receivedAt`
 * 降順の 50 件単位ページング（`shared/inbound/listing.py::sort_by_received_at_desc`
 * と `paginate` で構築済の純粋契約に依拠）。`nextToken` がページング
 * トークンで、最終ページでは null。
 *
 * 設計判断：
 *   - `createAuthFetch` 経由で Authorization ヘッダを付与する（既存
 *     `CycleClient` / `EmployeeClient` / `RecordingClient` と同じ DI 構造）。
 *   - 19原則(b)：HTTP エラーは握り潰さず `InboundApiError` として throw。
 *   - レスポンス形状（`items` / `pageSize` / `nextToken`）は `response_api`
 *     の `CycleResponsesPage` と同型（バックエンドが listing.paginate を
 *     使ってトークン化する設計に揃える）。
 *   - 各行は backend ハンドラ側で次の正規化を済ませて返す前提：
 *       * `callerNumberMasked`：Requirement 16.4 のマスキング済形式
 *         （`+` と末尾 4 桁を除き `*` で置換）。SPA 側で再マスクしない。
 *       * `cycleId` / `employeeId` / `employeeName`：未登録番号や
 *         CYCLE_TERMINATED の場合は null。
 *       * `flow`：InboundHandler が確定した 4 値文字列。
 *       * `voiceStatus`：判定済の Voice_Status。判定未確定や録音なしは null。
 *       * `transcriptExcerpt`：KeywordMatcher が書き出す抜粋。null 可。
 *   - 19原則(a) DRY：shape 不正検知 / serverMessage 抽出 / parseJsonBody
 *     はクライアントごとに同型のため独立実装するが、命名を `CycleApiError`
 *     と揃えて将来共通化しやすい形にする。
 */

import { createAuthFetch, getApiBaseUrl, type AuthFetch } from './httpClient';

/**
 * InboundHandler が確定する flow 4 値（Property 11 と整合）。
 *
 * - `ACTIVE_CYCLE`: 直近 30 日内に RUNNING または完了済 Cycle が存在し、
 *   Inbound_Handler が当該 Cycle 内の Response を更新した（録音 / Transcript あり）。
 * - `NO_CYCLE`: 発信者番号は登録済だが、紐付けるべき Cycle が存在しない。
 *   ガイダンス再生 + 切断のみで録音 / Transcript は基本的に存在しない。
 * - `NOT_REGISTERED`: Caller ID が Employee_Master に存在しない。録音なし。
 * - `CYCLE_TERMINATED`: 直近 Cycle が TIMEOUT / START_FAILED 等。Inbound_Contact
 *   のみ記録、Response 更新なし。録音は事案によって有無あり。
 */
export type InboundFlow = 'ACTIVE_CYCLE' | 'NO_CYCLE' | 'NOT_REGISTERED' | 'CYCLE_TERMINATED';

const VALID_FLOWS: readonly InboundFlow[] = [
  'ACTIVE_CYCLE',
  'NO_CYCLE',
  'NOT_REGISTERED',
  'CYCLE_TERMINATED',
];

function isInboundFlow(v: unknown): v is InboundFlow {
  return typeof v === 'string' && (VALID_FLOWS as readonly string[]).includes(v);
}

/** `GET /inbound` の各行（着信履歴一覧表示用）。 */
export interface InboundContactRow {
  /** Connect ContactId（UUID）。録音 / Transcript の S3 キー基となる。 */
  readonly contactId: string;
  /** 着信時刻（ISO 8601、UTC）。降順ソートのキー。 */
  readonly receivedAt: string;
  /** Requirement 16.4 マスキング済の発信者番号。 */
  readonly callerNumberMasked: string;
  /** 紐付いた Cycle ID。NOT_REGISTERED / NO_CYCLE では null。 */
  readonly cycleId: string | null;
  /** 紐付いた社員 ID。NOT_REGISTERED では null。 */
  readonly employeeId: string | null;
  /** 紐付いた社員氏名。NOT_REGISTERED では null。 */
  readonly employeeName: string | null;
  /** InboundHandler が確定した flow（4 値）。 */
  readonly flow: InboundFlow;
  /** Voice_Status（KeywordMatcher 判定後）。未確定 / 録音なしは null。 */
  readonly voiceStatus: string | null;
  /** Transcript 本文の抜粋（KeywordMatcher 書出）。null 可。 */
  readonly transcriptExcerpt: string | null;
}

/** `GET /inbound` のレスポンス。 */
export interface InboundContactsPage {
  readonly items: readonly InboundContactRow[];
  readonly pageSize: number;
  /** 次ページ取得用トークン。null なら最終ページ。 */
  readonly nextToken: string | null;
}

/** HTTP エラー詳細を保持する例外。UI 層がメッセージ翻訳に使う。 */
export class InboundApiError extends Error {
  readonly status: number;
  readonly serverMessage: string;

  constructor(status: number, serverMessage: string) {
    super(`HTTP ${status.toString()}: ${serverMessage}`);
    this.name = 'InboundApiError';
    this.status = status;
    this.serverMessage = serverMessage;
  }
}

export interface InboundClientOptions {
  /** テスト DI：認証付き fetch。未指定なら `createAuthFetch()`。 */
  readonly authFetch?: AuthFetch;
  /** テスト DI：API ベース URL。未指定なら `getApiBaseUrl()`。 */
  readonly baseUrl?: string;
}

/**
 * インバウンド API を集約したクライアント。
 *
 * シングルトンは作らず、呼出側で生成する（テストでフェイク差替を容易にするため）。
 */
export class InboundClient {
  private readonly fetchImpl: AuthFetch;
  private readonly baseUrl: string;

  constructor(options: InboundClientOptions = {}) {
    this.fetchImpl = options.authFetch ?? createAuthFetch();
    this.baseUrl = options.baseUrl ?? getApiBaseUrl();
  }

  /**
   * 着信履歴一覧を取得する。
   *
   * @param nextToken 前回応答の `nextToken`（未指定で先頭ページ）。
   * @returns 受信時刻降順 50 件の 1 ページと次ページトークン。
   */
  async list(nextToken?: string): Promise<InboundContactsPage> {
    const base = `${this.baseUrl}/inbound`;
    const url =
      nextToken === undefined || nextToken === ''
        ? base
        : `${base}?nextToken=${encodeURIComponent(nextToken)}`;
    const res = await this.fetchImpl(url, { method: 'GET' });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new InboundApiError(res.status, extractServerMessage(body));
    }
    return readInboundContactsPage(body);
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

function readInboundContactsPage(body: unknown): InboundContactsPage {
  if (body === null || typeof body !== 'object') {
    throw new InboundApiError(0, 'Unexpected response shape (root not object)');
  }
  const obj = body as Record<string, unknown>;
  const rawItems = obj.items;
  if (!Array.isArray(rawItems)) {
    throw new InboundApiError(0, 'Unexpected response shape (items not array)');
  }
  const items: InboundContactRow[] = rawItems.map((row, idx) => readInboundContactRow(row, idx));
  const pageSize = typeof obj.pageSize === 'number' ? obj.pageSize : items.length;
  const nextRaw = obj.nextToken;
  const nextToken = typeof nextRaw === 'string' && nextRaw !== '' ? nextRaw : null;
  return { items, pageSize, nextToken };
}

function readInboundContactRow(raw: unknown, idx: number): InboundContactRow {
  if (raw === null || typeof raw !== 'object') {
    throw new InboundApiError(0, `Unexpected inbound row shape at index ${idx.toString()}`);
  }
  const r = raw as Record<string, unknown>;
  const contactId = r.contactId;
  const receivedAt = r.receivedAt;
  const callerNumberMasked = r.callerNumberMasked;
  const flow = r.flow;
  if (
    typeof contactId !== 'string' ||
    typeof receivedAt !== 'string' ||
    typeof callerNumberMasked !== 'string' ||
    !isInboundFlow(flow)
  ) {
    throw new InboundApiError(
      0,
      `Unexpected inbound row fields at index ${idx.toString()} (contactId/receivedAt/callerNumberMasked/flow)`,
    );
  }
  return {
    contactId,
    receivedAt,
    callerNumberMasked,
    cycleId: typeof r.cycleId === 'string' ? r.cycleId : null,
    employeeId: typeof r.employeeId === 'string' ? r.employeeId : null,
    employeeName: typeof r.employeeName === 'string' ? r.employeeName : null,
    flow,
    voiceStatus: typeof r.voiceStatus === 'string' ? r.voiceStatus : null,
    transcriptExcerpt: typeof r.transcriptExcerpt === 'string' ? r.transcriptExcerpt : null,
  };
}
