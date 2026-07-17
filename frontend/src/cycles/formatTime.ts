/**
 * UTC の ISO 8601 文字列を日本時刻（JST = UTC+9）で表示用に変換するユーティリティ。
 *
 * バックエンドは全て UTC の ISO 8601 形式（末尾 Z）で返すため、
 * フロントエンドで日本時刻に変換して表示する。
 */

/**
 * ISO 8601 文字列を「YYYY/MM/DD HH:mm:ss」形式の日本時刻に変換する。
 * null/undefined/空文字列の場合は '-' を返す。
 */
export function formatJst(isoString: string | null | undefined): string {
  if (!isoString) return '-';
  try {
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return isoString;
    return date.toLocaleString('ja-JP', {
      timeZone: 'Asia/Tokyo',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return isoString;
  }
}
