/**
 * サイクル関連の内部値をユーザー向け日本語ラベルに変換するユーティリティ。
 *
 * URL パスや API フィールド名はそのまま維持し、画面表示のみ日本語化する。
 */

/** Cycle 上位ステータスの日本語ラベル。 */
export function formatCycleStatus(status: string): string {
  switch (status) {
    case 'RUNNING':
      return '実行中';
    case 'COMPLETED':
      return '完了';
    case 'TIMEOUT':
      return 'タイムアウト';
    case 'START_FAILED':
      return '起動失敗';
    default:
      return status;
  }
}

/** Cycle mode の日本語ラベル。 */
export function formatCycleMode(mode: string | null | undefined): string {
  if (mode === null || mode === undefined) return '-';
  switch (mode) {
    case 'ALL':
      return '全員';
    case 'UNREACHABLE_ONLY':
      return '未到達者のみ';
    default:
      return mode;
  }
}

/** Voice_Status の日本語ラベル。 */
export function formatVoiceStatus(status: string | null | undefined): string {
  if (status === null || status === undefined) return '-';
  switch (status) {
    case 'SAFE':
      return '無事';
    case 'INJURED':
      return '怪我';
    case 'UNAVAILABLE':
      return '行動不能';
    case 'OTHER':
      return 'その他';
    case 'UNREACHABLE':
      return '未到達';
    case 'PENDING':
      return '未応答';
    default:
      return status;
  }
}
