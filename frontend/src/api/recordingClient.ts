/**
 * 録音 / Transcript 署名付き URL 取得 API クライアント（Phase 10.7）。
 *
 * バックエンド `backend/lambdas/recording_api/handler.py` の以下ルートに対応する：
 *   - GET /cycles/{id}/recordings/{employeeId}/{seq}
 *   - GET /cycles/{id}/transcripts/{employeeId}/{seq}
 *   - GET /inbound/{contactId}/recording        (Phase 10.8 で使用)
 *   - GET /inbound/{contactId}/transcript       (Phase 10.8 で使用)
 *
 * 設計判断：
 *   - `createAuthFetch` 経由で Authorization ヘッダを付与する。
 *     `EmployeeClient` / `CycleClient` と同じ DI 構造を踏襲する。
 *   - 19原則(b)：HTTP エラーは握り潰さず `RecordingApiError` として throw。
 *     とくに 410 Gone（90 日経過）は呼出側が「保管期間超過」メッセージへ
 *     翻訳する必要があるため、`isGone()` ヘルパで判別可能にしておく。
 *   - 戻り型はバックエンド handler の JSON 出力スキーマ（`url` / `expiresInSeconds`
 *     / `bucket` / `key`）と厳密に一致させる。
 */

import { createAuthFetch, getApiBaseUrl, type AuthFetch } from './httpClient';

/** バックエンドが返す署名付き URL 応答。 */
export interface PresignedArtifact {
  /** 署名付き GET URL。 */
  readonly url: string;
  /** 有効期限（秒）。Requirement 12.2 により 15 分（900 秒）想定。 */
  readonly expiresInSeconds: number;
  /** S3 バケット名（参考表示用）。 */
  readonly bucket: string;
  /** S3 オブジェクトキー（参考表示用）。 */
  readonly key: string;
}

/** HTTP エラー詳細を保持する例外。 */
export class RecordingApiError extends Error {
  readonly status: number;
  readonly serverMessage: string;
  /** 410 のとき backend が同梱する基準日時（`startedAt` or `receivedAt`）。 */
  readonly referenceTimestamp?: string;

  constructor(status: number, serverMessage: string, referenceTimestamp?: string) {
    super(`HTTP ${status.toString()}: ${serverMessage}`);
    this.name = 'RecordingApiError';
    this.status = status;
    this.serverMessage = serverMessage;
    if (referenceTimestamp !== undefined) {
      this.referenceTimestamp = referenceTimestamp;
    }
  }

  /** 90 日経過によるリソース消失（Requirement 12.3）。 */
  isGone(): boolean {
    return this.status === 410;
  }
}

export interface RecordingClientOptions {
  /** テスト DI：認証付き fetch。未指定なら `createAuthFetch()`。 */
  readonly authFetch?: AuthFetch;
  /** テスト DI：API ベース URL。未指定なら `getApiBaseUrl()`。 */
  readonly baseUrl?: string;
}

export class RecordingClient {
  private readonly fetchImpl: AuthFetch;
  private readonly baseUrl: string;

  constructor(options: RecordingClientOptions = {}) {
    this.fetchImpl = options.authFetch ?? createAuthFetch();
    this.baseUrl = options.baseUrl ?? getApiBaseUrl();
  }

  /**
   * Cycle 内の録音 (.wav) 用署名付き URL を取得する。
   *
   * @param cycleId 対象 Cycle ID。
   * @param employeeId 対象社員 ID。
   * @param seq 通話シーケンス番号（1-indexed の文字列）。
   */
  async getCycleRecording(
    cycleId: string,
    employeeId: string,
    seq: string,
  ): Promise<PresignedArtifact> {
    const url =
      `${this.baseUrl}/cycles/${encodeURIComponent(cycleId)}/recordings/` +
      `${encodeURIComponent(employeeId)}/${encodeURIComponent(seq)}`;
    return this.fetchPresigned(url);
  }

  /**
   * Cycle 内の Transcript (.json) 用署名付き URL を取得する。
   */
  async getCycleTranscript(
    cycleId: string,
    employeeId: string,
    seq: string,
  ): Promise<PresignedArtifact> {
    const url =
      `${this.baseUrl}/cycles/${encodeURIComponent(cycleId)}/transcripts/` +
      `${encodeURIComponent(employeeId)}/${encodeURIComponent(seq)}`;
    return this.fetchPresigned(url);
  }

  /**
   * インバウンド着信の録音 (.wav) 用署名付き URL を取得する（Phase 10.8）。
   *
   * バックエンド `GET /inbound/{contactId}/recording`（recording_api/handler.py の
   * `_inbound_artifact`）と対応。90 日経過は backend が 410 + `receivedAt` を返却。
   *
   * @param contactId Connect ContactId。
   */
  async getInboundRecording(contactId: string): Promise<PresignedArtifact> {
    const url = `${this.baseUrl}/inbound/${encodeURIComponent(contactId)}/recording`;
    return this.fetchPresigned(url);
  }

  /**
   * インバウンド着信の Transcript (.json) 用署名付き URL を取得する（Phase 10.8）。
   *
   * バックエンド `GET /inbound/{contactId}/transcript` と対応。
   */
  async getInboundTranscript(contactId: string): Promise<PresignedArtifact> {
    const url = `${this.baseUrl}/inbound/${encodeURIComponent(contactId)}/transcript`;
    return this.fetchPresigned(url);
  }

  private async fetchPresigned(fullUrl: string): Promise<PresignedArtifact> {
    const res = await this.fetchImpl(fullUrl, { method: 'GET' });
    const body = await parseJsonBody(res);
    if (!res.ok) {
      const serverMessage = extractServerMessage(body);
      const ref = extractReferenceTimestamp(body);
      throw new RecordingApiError(res.status, serverMessage, ref);
    }
    return readPresignedArtifact(body);
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

function extractReferenceTimestamp(body: unknown): string | undefined {
  if (body === null || typeof body !== 'object') return undefined;
  const obj = body as Record<string, unknown>;
  // backend は cycle 系で startedAt、inbound 系で receivedAt を返す。
  if (typeof obj.startedAt === 'string') return obj.startedAt;
  if (typeof obj.receivedAt === 'string') return obj.receivedAt;
  return undefined;
}

function readPresignedArtifact(body: unknown): PresignedArtifact {
  if (body === null || typeof body !== 'object') {
    throw new RecordingApiError(0, 'Unexpected response shape (root not object)');
  }
  const obj = body as Record<string, unknown>;
  const url = obj.url;
  const expiresInSeconds = obj.expiresInSeconds;
  const bucket = obj.bucket;
  const key = obj.key;
  if (
    typeof url !== 'string' ||
    typeof expiresInSeconds !== 'number' ||
    typeof bucket !== 'string' ||
    typeof key !== 'string'
  ) {
    throw new RecordingApiError(0, 'Unexpected response shape (presigned fields)');
  }
  return { url, expiresInSeconds, bucket, key };
}
