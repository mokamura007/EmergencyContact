/**
 * `NewPasswordRequiredChallenge` のページ間受け渡し用モジュールスコープストア。
 *
 * 背景（issue #3 再々修正、2026-07-10）：
 *   `LoginPage` → `NewPasswordPage` への遷移で、初版は
 *   `navigate('/new-password', { state: { challenge } })` を使用していたが、
 *   React Router は history.state に対して `structuredClone` を実施するため、
 *   `challenge.complete` 関数が含まれる本オブジェクトは `DataCloneError`
 *   を投げてしまい遷移に失敗していた（Web API 仕様：`structuredClone` は
 *   Function を clone できない）。
 *
 * 対応：challenge を history.state に載せず、モジュールスコープの一時変数で
 *   保持する。ページ間遷移は React Router の通常 navigate（state なし）で行う。
 *
 * 特性：
 *   - `set` で書き込み、`consume` で読み取り + 消去（1 回のみ利用可能）。
 *   - ブラウザリロードで自然に喪失する（Cognito 側でも challenge は既に
 *     `newPasswordRequired` コールバック 1 回に紐づくため、再利用しない前提）。
 *   - 複数タブ間では共有されない（各タブが独立の JS ランタイム）。
 *
 * 設計判断：
 *   - SessionStorage / LocalStorage は関数を serialize できないため使えない。
 *   - React Context は provider の再マウントで喪失する可能性があるため
 *     Router 経由の遷移ケースに合わない。
 *   - シンプルにモジュールスコープの mutable 変数で保持する（DRY、フォール
 *     バック禁止＝空値時は null を返し UI 側で `/login` にリダイレクト）。
 */

import type { NewPasswordRequiredChallenge } from './types';

let pendingChallenge: NewPasswordRequiredChallenge | null = null;

/**
 * challenge を一時ストアに書き込む。既存の値は上書きされる。
 */
export function setPendingChallenge(challenge: NewPasswordRequiredChallenge): void {
  pendingChallenge = challenge;
}

/**
 * challenge を取り出して消去する。存在しない場合は null。
 * `NewPasswordPage` のマウント時に 1 回だけ呼ぶ想定。
 */
export function consumePendingChallenge(): NewPasswordRequiredChallenge | null {
  const challenge = pendingChallenge;
  pendingChallenge = null;
  return challenge;
}

/**
 * challenge を破棄する（consume を伴わないクリア）。
 * ログアウトやアンマウント時に安全のため呼ぶ。
 */
export function clearPendingChallenge(): void {
  pendingChallenge = null;
}
