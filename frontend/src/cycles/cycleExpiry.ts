/**
 * 90 日保管期間判定（Phase 10.7、Requirement 12.2 / 12.3）。
 *
 * 録音 / Transcript は Cycle 起動時刻（または Inbound 着信時刻）から 90 日で
 * S3 ライフサイクル削除される。SPA はこの境界を事前判定して再生ボタンや
 * Transcript リンクの有効・無効を切り替える。
 *
 * 設計判断：
 *   - 純粋関数。`Date.now()` は注入する（テストで決定論的に検証可能）。
 *   - サーバー側の判定は `recording_api/handler.py::can_issue_url` が担う。
 *     SPA 側は UX 上のプリフライト（押下不可表示）に限定し、最終判断は
 *     必ずサーバーの 410 応答に従う（19原則(b)：エラーはエラーのまま返す）。
 *   - 90 日 = 90 * 24 * 3600 * 1000 ms。閏秒は無視。
 */

/** 90 日（ミリ秒）。 */
export const RETENTION_WINDOW_MS = 90 * 24 * 60 * 60 * 1000;

/**
 * `referenceIso` から 90 日が経過していない（= 録音 / Transcript が再生可能）か判定する。
 *
 * @param referenceIso 基準時刻の ISO 8601 文字列（Cycle.startedAt または
 *                     InboundContact.receivedAt）。空文字や parse 不能なら
 *                     false（= 不可）を返す。
 * @param now 現在時刻。
 * @returns 90 日以内なら true、超過 / 不正入力なら false。
 */
export function isWithinRetentionWindow(referenceIso: string, now: Date): boolean {
  if (referenceIso === '') return false;
  const ms = Date.parse(referenceIso);
  if (Number.isNaN(ms)) return false;
  const diff = now.getTime() - ms;
  if (diff < 0) return true; // 未来日時は安全側で「保管中」扱い
  return diff <= RETENTION_WINDOW_MS;
}

/**
 * 90 日を超過しているか（再生ボタン無効化の判定に使う）。
 *
 * `isWithinRetentionWindow` の対偶 + 不正入力は「超過扱いしない」とする
 * （= ボタン押下を許してサーバー応答で確実な 410 を取得する）。
 */
export function isRetentionExpired(referenceIso: string, now: Date): boolean {
  if (referenceIso === '') return false;
  const ms = Date.parse(referenceIso);
  if (Number.isNaN(ms)) return false;
  const diff = now.getTime() - ms;
  return diff > RETENTION_WINDOW_MS;
}
