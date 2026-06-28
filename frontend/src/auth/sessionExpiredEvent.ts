/**
 * セッション期限切れ通知の subscribe / notify 機構。
 *
 * タスク 10.2 の要件「リフレッシュトークンによる自動更新、期限切れ時は
 * 再ログインへ誘導」を実装するための疎結合エントリポイント。
 *
 * 設計判断：
 *   - 認証層は UI に依存しないため、URL リダイレクトや React Router の
 *     ナビゲーション関数を直接呼ぶことはしない。代わりに subscriber
 *     パターンで「セッションが切れた」事実だけを通知する。
 *   - 実際の再ログイン誘導（リダイレクト / モーダル表示）は Phase 10.3 の
 *     ルーティング層が `subscribeSessionExpired` で受信して実装する。
 *   - 二重通知抑止のためのデバウンスは UI 層責務とする（ここでは通知を
 *     そのまま伝播するシンプルな実装にとどめる、DRY のため）。
 */

type Listener = () => void;

const listeners = new Set<Listener>();

/**
 * セッション期限切れイベントを購読する。
 * @returns 解除用関数。
 */
export function subscribeSessionExpired(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * セッション期限切れを全リスナーへ通知する。
 *
 * 個別リスナーの例外で他のリスナーが呼ばれなくなるのを防ぐため、
 * 例外は `console.error` のみで吸収する（fail-fast すべき API 層の
 * エラーは別経路で扱う方針）。
 */
export function notifySessionExpired(): void {
  for (const listener of listeners) {
    try {
      listener();
    } catch (err) {
      console.error('[auth] sessionExpired listener threw an error', err);
    }
  }
}

/**
 * テスト用：登録されたリスナーを全て解除する。本番コードから呼ばない。
 */
export function _resetSessionExpiredListenersForTest(): void {
  listeners.clear();
}
