/**
 * サイクル管理 API クライアント（Phase 10.5 / 10.6 / 10.7）。
 *
 * バックエンド `backend/lambdas/cycle_api/handler.py` および
 * `backend/lambdas/response_api/handler.py` の以下ルートに対応する：
 *   - POST /cycles                       : 新規サイクル起動（Idempotency-Key ヘッダで冪等性確保）
 *   - GET  /cycles                       : サイクル一覧（バックエンドは startedAt 降順の全件を返す）
 *   - GET  /cycles/{id}                  : サイクル詳細
 *   - GET  /cycles/{id}/status           : 集計値 + 個別ステータス（ポーリング用、Phase 10.6）
 *   - GET  /cycles/{id}/responses        : 社員別 Response 一覧（50 件ページング、Phase 10.7）
 *
 * 設計判断：
 *   - `createAuthFetch` を経由して Authorization ヘッダを付与する。
 *     `EmployeeClient` と同じ DI 構造（`authFetch` / `baseUrl`）を踏襲する。
 *   - 19原則(b)：HTTP エラーは握り潰さず `CycleApiError` として throw し、
 *     ステータスコード・サーバーメッセージを保持する。
 *   - 戻り型はバックエンド handler の JSON 出力スキーマと厳密に一致させる
 *     （DRY 原則：仕様は backend handler を正とし、本層はそれをそのまま受ける）。
 *   - `getStatus` は `AbortSignal` を受け取る：Status_Viewer の 10 秒ポーリングで
 *     前回呼出進行中に次の tick が来たら abort して重複呼出を抑止する（Requirement 11.1）。
 *   - `list()` のページングは backend が一括で startedAt 降順を返す現状契約に合わせ、
 *     SPA 側で 50 件単位にスライスする（Phase 10.7 / Requirement 12.1）。
 */

import {
  type CycleDegradedEntry,
  type CycleStatusItem,
  type CycleStatusSnapshot,
  type CycleStatusSummary,
  type CycleTopStatus,
  type VoiceStatusValue,
} from '../cycles/statusReducer';

import { createAuthFetch, getApiBaseUrl, type AuthFetch } from './httpClient';

export type {
  CycleDegradedEntry,
  CycleStatusItem,
  CycleStatusSnapshot,
  CycleStatusSummary,
  CycleTopStatus,
  VoiceStatusValue,
};

/** `POST /cycles` のリクエストボディ。 */
export interface CreateCyclePayload {
  /** 対象者選定モード。`ALL` または `UNREACHABLE_ONLY`。 */
  readonly mode: CycleMode;
  /** Retry_Count（0〜5）。 */
  readonly retryCount: number;
  /** Retry_Interval（分、1〜60）。 */
  readonly retryIntervalMinutes: number;
  /** mode=UNREACHABLE_ONLY のときに参照する直近完了 Cycle ID（任意）。 */
  readonly referencedCycleId?: string;
}

export type CycleMode = 'ALL' | 'UNREACHABLE_ONLY';

/** `POST /cycles` の成功レスポンス（201 新規 / 200 idempotent replay）。 */
export interface CreateCycleResult {
  readonly cycleId: string;
  readonly status: string;
  readonly startedAt: string;
  readonly dictionaryVersion: number;
  /** 新規起動のときのみ存在し、replay 時は undefined。 */
  readonly mode?: CycleMode;
  /** Idempotency-Key 再送による既存サイクル返却の場合 true。 */
  readonly idempotentReplay?: boolean;
}

/**
 * `GET /cycles` の各行（履歴一覧表示用）。バックエンドが返す startedAt 降順をそのまま使う。
 */
export interface CycleSummary {
  readonly cycleId: string;
  readonly status: string;
  readonly mode: string | null;
  readonly startedAt: string;
  readonly completedAt: string | null;
  readonly dictionaryVersion: number;
}

/** `GET /cycles` のレスポンス。 */
export interface ListCyclesResult {
  readonly cycles: readonly CycleSummary[];
  readonly total: number;
}

/**
 * `GET /cycles/{id}` の Cycle 詳細。
 * バックエンド `_get_cycle_detail` が DynamoDB の Item をそのまま返す契約のため、
 * クライアント側ではコア項目だけを厳密にパースし、その他は `extra` 経由で参照する。
 */
export interface CycleDetail {
  readonly cycleId: string;
  readonly status: string;
  readonly mode: string | null;
  readonly startedAt: string;
  readonly completedAt: string | null;
  readonly dictionaryVersion: number;
  readonly retryCount: number | null;
  readonly retryIntervalMinutes: number | null;
  readonly targetCount: number | null;
  readonly referencedCycleId: string | null;
  /** 上記で正規化していない属性（DynamoDB の生フィールド）。 */
  readonly extra: Readonly<Record<string, unknown>>;
}

/** `GET /cycles/{id}/responses` の各行（履歴閲覧画面の社員別 Response）。 */
export interface CycleResponseRow {
  readonly cycleId: string;
  readonly employeeId: string;
  readonly employeeName: string | null;
  readonly voiceStatus: string | null;
  readonly callResultCode: string | null;
  /** 累計発信回数（再試行も含む通算）。録音 / Transcript の seq として使う。 */
  readonly retryCount: number;
  readonly lastCalledAt: string | null;
  readonly finalizedAt: string | null;
  readonly transcriptExcerpt: string | null;
}

/** `GET /cycles/{id}/responses` のレスポンス。 */
export interface CycleResponsesPage {
  readonly items: readonly CycleResponseRow[];
  readonly pageSize: number;
  /** 次ページ取得用トークン。null なら最終ページ。 */
  readonly nextToken: string | null;
}

/** HTTP エラー詳細を保持する例外。UI 層がメッセージ翻訳に使う。 */
export class CycleApiError extends Error {
  readonly status: number;
  readonly serverMessage: string;
  /** サーバーが返した cycleId（START_FAILED で 500 が返る場合等）。 */
  readonly cycleId?: string;

  constructor(status: number, serverMessage: string, cycleId?: string) {
    super(`HTTP ${status.toString()}: ${serverMessage}`);
    this.name = 'CycleApiError';
    this.status = status;
    this.serverMessage = serverMessage;
    if (cycleId !== undefined) {
      this.cycleId = cycleId;
    }
  }
}

export interface CycleClientOptions {
  /** テスト DI：認証付き fetch。未指定なら `createAuthFetch()` を使う。 */
  readonly authFetch?: AuthFetch;
  /** テスト DI：API ベース URL。未指定なら `getApiBaseUrl()` を使う。 */
  readonly baseUrl?: string;
}

/**
 * サイクル API を集約したクライアント。
 *
 * シングルトンは作らず、呼出側で生成する（テストでフェイク差替を容易にするため）。
 */
export class CycleClient {
  private readonly fetchImpl: AuthFetch;
  private readonly baseUrl: string;

  constructor(options: CycleClientOptions = {}) {
    this.fetchImpl = options.authFetch ?? createAuthFetch();
    this.baseUrl = options.baseUrl ?? getApiBaseUrl();
  }

  /**
   * 新規サイクルを起動する。
   *
   * @param payload リクエストボディ。
   * @param idempotencyKey `Idempotency-Key` ヘッダ値（UUID v4 想定）。
   * @returns 起動結果（cycleId / dictionaryVersion 等）。
   */
  async create(payload: CreateCyclePayload, idempotencyKey: string): Promise<CreateCycleResult> {
    const res = await this.fetchImpl(`${this.baseUrl}/cycles`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(payload),
    });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new CycleApiError(res.status, extractServerMessage(body), extractCycleId(body));
    }
    return readCreateCycleResult(body);
  }

  /**
   * 進行中サイクルの集計値 + 個別ステータスを取得する（Phase 10.6）。
   *
   * バックエンド `cycle_api/handler.py` の `GET /cycles/{id}/status` を呼ぶ。
   *
   * @param cycleId 取得対象 Cycle ID。
   * @param signal 中断シグナル。Status_Viewer のポーリングで重複呼出を抑止する
   *               ために `AbortController.signal` を渡す（任意）。
   * @returns 正規化済スナップショット。
   */
  async getStatus(cycleId: string, signal?: AbortSignal): Promise<CycleStatusSnapshot> {
    const url = `${this.baseUrl}/cycles/${encodeURIComponent(cycleId)}/status`;
    const init: RequestInit = { method: 'GET' };
    if (signal !== undefined) {
      init.signal = signal;
    }
    const res = await this.fetchImpl(url, init);
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new CycleApiError(res.status, extractServerMessage(body), extractCycleId(body));
    }
    return readCycleStatusSnapshot(body);
  }

  /**
   * サイクル履歴一覧を取得する（Phase 10.7、Requirement 12.1）。
   *
   * バックエンド `cycle_api/handler.py` の `GET /cycles` を呼ぶ。
   * 現契約では全件を startedAt 降順で返す（サーバ側ページング無し）ため、
   * SPA 側でのページング（50 件単位）は呼出側 UI コンポーネントの責務とする。
   */
  async list(): Promise<ListCyclesResult> {
    const res = await this.fetchImpl(`${this.baseUrl}/cycles`, { method: 'GET' });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new CycleApiError(res.status, extractServerMessage(body));
    }
    return readListCyclesResult(body);
  }

  /**
   * Cycle 詳細を取得する（Phase 10.7、Requirement 12.1）。
   *
   * バックエンド `cycle_api/handler.py` の `GET /cycles/{id}` を呼ぶ。
   */
  async getDetail(cycleId: string): Promise<CycleDetail> {
    const url = `${this.baseUrl}/cycles/${encodeURIComponent(cycleId)}`;
    const res = await this.fetchImpl(url, { method: 'GET' });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new CycleApiError(res.status, extractServerMessage(body), extractCycleId(body));
    }
    return readCycleDetail(body);
  }

  /**
   * Cycle に紐づく社員別 Response 一覧を取得する（Phase 10.7、Requirement 12.1）。
   *
   * バックエンド `response_api/handler.py` の `GET /cycles/{id}/responses` を呼ぶ。
   * 50 件単位でページングされ、続きがある場合は `nextToken` を次回呼出に渡す。
   *
   * @param cycleId 対象 Cycle ID。
   * @param nextToken 前回応答の `nextToken`（未指定で先頭ページ）。
   */
  async listResponses(cycleId: string, nextToken?: string): Promise<CycleResponsesPage> {
    const base = `${this.baseUrl}/cycles/${encodeURIComponent(cycleId)}/responses`;
    const url =
      nextToken === undefined || nextToken === ''
        ? base
        : `${base}?nextToken=${encodeURIComponent(nextToken)}`;
    const res = await this.fetchImpl(url, { method: 'GET' });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      throw new CycleApiError(res.status, extractServerMessage(body), extractCycleId(body));
    }
    return readCycleResponsesPage(body);
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

function extractCycleId(body: unknown): string | undefined {
  if (body === null || typeof body !== 'object') return undefined;
  const value = (body as Record<string, unknown>).cycleId;
  return typeof value === 'string' ? value : undefined;
}

function readCreateCycleResult(body: unknown): CreateCycleResult {
  if (body === null || typeof body !== 'object') {
    throw new CycleApiError(0, 'Unexpected response shape (root not object)');
  }
  const obj = body as Record<string, unknown>;
  const cycleId = obj.cycleId;
  const status = obj.status;
  const startedAt = obj.startedAt;
  const dictionaryVersion = obj.dictionaryVersion;
  if (
    typeof cycleId !== 'string' ||
    typeof status !== 'string' ||
    typeof startedAt !== 'string' ||
    typeof dictionaryVersion !== 'number'
  ) {
    throw new CycleApiError(0, 'Unexpected response shape (cycle fields)');
  }
  const result: CreateCycleResult = {
    cycleId,
    status,
    startedAt,
    dictionaryVersion,
    ...(obj.mode === 'ALL' || obj.mode === 'UNREACHABLE_ONLY' ? { mode: obj.mode } : {}),
    ...(obj.idempotentReplay === true ? { idempotentReplay: true } : {}),
  };
  return result;
}

const VALID_TOP_STATUSES: readonly CycleTopStatus[] = [
  'RUNNING',
  'COMPLETED',
  'TIMEOUT',
  'START_FAILED',
];

const VALID_VOICE_STATUSES: readonly VoiceStatusValue[] = [
  'SAFE',
  'INJURED',
  'UNAVAILABLE',
  'OTHER',
  'UNREACHABLE',
  'PENDING',
];

function readCycleStatusSnapshot(body: unknown): CycleStatusSnapshot {
  if (body === null || typeof body !== 'object') {
    throw new CycleApiError(0, 'Unexpected status response shape (root not object)');
  }
  const obj = body as Record<string, unknown>;
  const cycleId = obj.cycleId;
  const status = obj.status;
  if (typeof cycleId !== 'string') {
    throw new CycleApiError(0, 'Unexpected status response shape (cycleId)');
  }
  if (typeof status !== 'string' || !isCycleTopStatus(status)) {
    throw new CycleApiError(0, `Unexpected status response shape (status=${String(status)})`);
  }
  return {
    cycleId,
    status,
    summary: readSummary(obj.summary),
    items: readItems(obj.items),
    degraded: readDegraded(obj.degraded),
  };
}

function isCycleTopStatus(v: string): v is CycleTopStatus {
  return (VALID_TOP_STATUSES as readonly string[]).includes(v);
}

function isVoiceStatusValue(v: string): v is VoiceStatusValue {
  return (VALID_VOICE_STATUSES as readonly string[]).includes(v);
}

function readSummary(raw: unknown): CycleStatusSummary {
  if (raw === null || typeof raw !== 'object') {
    throw new CycleApiError(0, 'Unexpected status response shape (summary not object)');
  }
  const s = raw as Record<string, unknown>;
  const targetTotal = s.targetTotal;
  const dispatched = s.dispatched;
  const responded = s.responded;
  const unreachable = s.unreachable;
  const byStatus = s.byStatus;
  if (
    typeof targetTotal !== 'number' ||
    typeof dispatched !== 'number' ||
    typeof responded !== 'number' ||
    typeof unreachable !== 'number'
  ) {
    throw new CycleApiError(0, 'Unexpected status response shape (summary numeric fields)');
  }
  if (byStatus === null || typeof byStatus !== 'object' || Array.isArray(byStatus)) {
    throw new CycleApiError(0, 'Unexpected status response shape (byStatus)');
  }
  const normalizedByStatus: Record<string, number> = {};
  for (const [k, v] of Object.entries(byStatus as Record<string, unknown>)) {
    if (typeof v !== 'number') {
      throw new CycleApiError(0, `Unexpected status response shape (byStatus.${k} not number)`);
    }
    normalizedByStatus[k] = v;
  }
  return { targetTotal, dispatched, responded, unreachable, byStatus: normalizedByStatus };
}

function readItems(raw: unknown): readonly CycleStatusItem[] {
  if (!Array.isArray(raw)) {
    throw new CycleApiError(0, 'Unexpected status response shape (items not array)');
  }
  return raw.map((row, idx) => readItem(row, idx));
}

function readItem(raw: unknown, idx: number): CycleStatusItem {
  if (raw === null || typeof raw !== 'object') {
    throw new CycleApiError(0, `Unexpected status response shape (items[${idx.toString()}])`);
  }
  const r = raw as Record<string, unknown>;
  const employeeId = r.employeeId;
  const name = r.name;
  const currentStatus = r.currentStatus;
  const callAttempts = r.callAttempts;
  const lastResponseAt = r.lastResponseAt;
  const transcriptExcerpt = r.transcriptExcerpt;
  if (
    typeof employeeId !== 'string' ||
    typeof name !== 'string' ||
    typeof currentStatus !== 'string' ||
    !isVoiceStatusValue(currentStatus) ||
    typeof callAttempts !== 'number' ||
    typeof transcriptExcerpt !== 'string' ||
    (lastResponseAt !== null && typeof lastResponseAt !== 'string')
  ) {
    throw new CycleApiError(
      0,
      `Unexpected status response shape (items[${idx.toString()}] fields)`,
    );
  }
  return {
    employeeId,
    name,
    currentStatus,
    callAttempts,
    lastResponseAt,
    transcriptExcerpt,
  };
}

function readDegraded(raw: unknown): readonly CycleDegradedEntry[] {
  if (raw === undefined) return []; // degraded フィールド省略時は空配列扱い
  if (!Array.isArray(raw)) {
    throw new CycleApiError(0, 'Unexpected status response shape (degraded not array)');
  }
  return raw.map((row, idx) => {
    if (row === null || typeof row !== 'object') {
      throw new CycleApiError(0, `Unexpected status response shape (degraded[${idx.toString()}])`);
    }
    const r = row as Record<string, unknown>;
    const component = r.component;
    const since = r.since;
    if (typeof component !== 'string' || typeof since !== 'string') {
      throw new CycleApiError(
        0,
        `Unexpected status response shape (degraded[${idx.toString()}] fields)`,
      );
    }
    return { component, since };
  });
}

// ---------- Phase 10.7: list / detail / responses パーサ ----------

function readListCyclesResult(body: unknown): ListCyclesResult {
  if (body === null || typeof body !== 'object') {
    throw new CycleApiError(0, 'Unexpected response shape (list root not object)');
  }
  const obj = body as Record<string, unknown>;
  const raw = obj.cycles;
  if (!Array.isArray(raw)) {
    throw new CycleApiError(0, 'Unexpected response shape (cycles not array)');
  }
  const cycles: CycleSummary[] = raw.map((item, idx) => readCycleSummary(item, idx));
  const total = typeof obj.total === 'number' ? obj.total : cycles.length;
  return { cycles, total };
}

function readCycleSummary(raw: unknown, idx: number): CycleSummary {
  if (raw === null || typeof raw !== 'object') {
    throw new CycleApiError(0, `Unexpected cycle summary shape at index ${idx.toString()}`);
  }
  const r = raw as Record<string, unknown>;
  const cycleId = r.cycleId;
  const status = r.status;
  const startedAt = r.startedAt;
  if (typeof cycleId !== 'string' || typeof status !== 'string' || typeof startedAt !== 'string') {
    throw new CycleApiError(0, `Unexpected cycle summary fields at index ${idx.toString()}`);
  }
  return {
    cycleId,
    status,
    mode: typeof r.mode === 'string' ? r.mode : null,
    startedAt,
    completedAt: typeof r.completedAt === 'string' ? r.completedAt : null,
    dictionaryVersion: typeof r.dictionaryVersion === 'number' ? r.dictionaryVersion : 0,
  };
}

function readCycleDetail(body: unknown): CycleDetail {
  if (body === null || typeof body !== 'object') {
    throw new CycleApiError(0, 'Unexpected cycle detail shape (root not object)');
  }
  const r = body as Record<string, unknown>;
  const cycleId = r.cycleId;
  const status = r.status;
  const startedAt = r.startedAt;
  if (typeof cycleId !== 'string' || typeof status !== 'string' || typeof startedAt !== 'string') {
    throw new CycleApiError(0, 'Unexpected cycle detail fields (cycleId/status/startedAt)');
  }
  const extra: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(r)) {
    if (
      k === 'cycleId' ||
      k === 'status' ||
      k === 'mode' ||
      k === 'startedAt' ||
      k === 'completedAt' ||
      k === 'dictionaryVersion' ||
      k === 'retryCount' ||
      k === 'retryIntervalMinutes' ||
      k === 'targetCount' ||
      k === 'referencedCycleId'
    ) {
      continue;
    }
    extra[k] = v;
  }
  return {
    cycleId,
    status,
    mode: typeof r.mode === 'string' ? r.mode : null,
    startedAt,
    completedAt: typeof r.completedAt === 'string' ? r.completedAt : null,
    dictionaryVersion: typeof r.dictionaryVersion === 'number' ? r.dictionaryVersion : 0,
    retryCount: typeof r.retryCount === 'number' ? r.retryCount : null,
    retryIntervalMinutes:
      typeof r.retryIntervalMinutes === 'number' ? r.retryIntervalMinutes : null,
    targetCount: typeof r.targetCount === 'number' ? r.targetCount : null,
    referencedCycleId: typeof r.referencedCycleId === 'string' ? r.referencedCycleId : null,
    extra,
  };
}

function readCycleResponsesPage(body: unknown): CycleResponsesPage {
  if (body === null || typeof body !== 'object') {
    throw new CycleApiError(0, 'Unexpected responses page shape (root not object)');
  }
  const obj = body as Record<string, unknown>;
  const rawItems = obj.items;
  if (!Array.isArray(rawItems)) {
    throw new CycleApiError(0, 'Unexpected responses page shape (items not array)');
  }
  const items: CycleResponseRow[] = rawItems.map((row, idx) => readCycleResponseRow(row, idx));
  const pageSize = typeof obj.pageSize === 'number' ? obj.pageSize : items.length;
  const nextRaw = obj.nextToken;
  const nextToken =
    typeof nextRaw === 'string' && nextRaw !== '' ? nextRaw : nextRaw === null ? null : null;
  return { items, pageSize, nextToken };
}

function readCycleResponseRow(raw: unknown, idx: number): CycleResponseRow {
  if (raw === null || typeof raw !== 'object') {
    throw new CycleApiError(0, `Unexpected response row shape at index ${idx.toString()}`);
  }
  const r = raw as Record<string, unknown>;
  const cycleId = r.cycleId;
  const employeeId = r.employeeId;
  if (typeof cycleId !== 'string' || typeof employeeId !== 'string') {
    throw new CycleApiError(
      0,
      `Unexpected response row fields at index ${idx.toString()} (cycleId/employeeId)`,
    );
  }
  return {
    cycleId,
    employeeId,
    employeeName: typeof r.employeeName === 'string' ? r.employeeName : null,
    voiceStatus: typeof r.voiceStatus === 'string' ? r.voiceStatus : null,
    callResultCode: typeof r.callResultCode === 'string' ? r.callResultCode : null,
    retryCount: typeof r.retryCount === 'number' ? r.retryCount : 0,
    lastCalledAt: typeof r.lastCalledAt === 'string' ? r.lastCalledAt : null,
    finalizedAt: typeof r.finalizedAt === 'string' ? r.finalizedAt : null,
    transcriptExcerpt: typeof r.transcriptExcerpt === 'string' ? r.transcriptExcerpt : null,
  };
}
