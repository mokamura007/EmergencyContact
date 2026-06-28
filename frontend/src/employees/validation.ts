/**
 * 社員マスタ管理 UI のクライアント側バリデーション。
 *
 * 対応要件：
 *   - Requirement 2.1 : 入力項目は氏名と電話番号のみ。
 *   - Requirement 2.7 : E.164（先頭 `+` + 数字 1〜15 桁）に準拠しない番号を拒否。
 *   - Requirement 3.1 : CSV は UTF-8。
 *   - Requirement 3.5 : ヘッダ必須、データ行 300 行以下、サイズ 1 MiB 以下。
 *
 * 設計判断：
 *   - 純粋関数のみで構成。UI から独立してテストできる（PBT 候補 Property 6
 *     と同じ事前条件を SPA でも壁として持つ）。
 *   - `backend/shared/employee/validate.py` の `is_valid_e164` /
 *     `is_valid_name` および `csv_parser.MAX_BYTES` / `MAX_DATA_ROWS` /
 *     `REQUIRED_HEADERS` と数値・形式を一致させる。差分が出ると
 *     「クライアントで通って API で 400」というユーザー体験崩れに直結するため、
 *     値はバックエンド側を正とする（変更時は両側を同時更新）。
 *   - 19原則(b)：フォールバックなし。バリデーション失敗時は明示的にエラー
 *     コード列を返し、UI 層がユーザー向けメッセージに翻訳する。
 */

/** E.164：先頭 `+` の直後に 1〜15 桁の十進数字、それ以上は不可。 */
const E164_REGEX = /^\+\d{1,15}$/;

/** 氏名の長さ上限（バックエンド `validate.py` MAX_NAME_LENGTH と一致）。 */
export const MAX_NAME_LENGTH = 100;

/** CSV ファイルサイズ上限（バックエンド `csv_parser.MAX_BYTES` と一致）。 */
export const CSV_MAX_BYTES = 1 * 1024 * 1024;

/** CSV データ行数上限（バックエンド `csv_parser.MAX_DATA_ROWS` と一致）。 */
export const CSV_MAX_DATA_ROWS = 300;

/** CSV ヘッダ行の期待値（バックエンド `csv_parser.REQUIRED_HEADERS` と一致）。 */
export const CSV_REQUIRED_HEADER = 'name,phoneNumber';

/**
 * E.164 形式の電話番号か判定する純粋関数。
 * 文字列以外は無条件で false。
 */
export function isValidE164(phone: unknown): phone is string {
  return typeof phone === 'string' && E164_REGEX.test(phone);
}

/**
 * 氏名として妥当か判定する純粋関数。
 * 文字列以外、または前後空白を除いた長さが 1〜MAX_NAME_LENGTH の外なら false。
 */
export function isValidName(name: unknown): name is string {
  if (typeof name !== 'string') return false;
  const trimmed = name.trim();
  return trimmed.length > 0 && trimmed.length <= MAX_NAME_LENGTH;
}

/**
 * CSV ファイル受入検査の結果。
 *
 * - `ok=true`：ファイルレベル制約を全て満たす（API への送信を許可）。
 * - `ok=false`：失敗理由を列挙して返す。UI 側はメッセージとして表示する。
 *
 * 注意：本検査はあくまで「事前防御」。実際の行ごとの E.164 / 重複検査は
 * バックエンドの `parse_employee_csv` が正となる。クライアント側で OK でも
 * 行レベルでバックエンドが 400 を返すことはあり得る。
 */
export interface CsvFileValidationResult {
  readonly ok: boolean;
  readonly errors: readonly string[];
}

/**
 * CSV ファイルの「ファイルレベル」事前検査。
 *
 * チェック対象：(a) UTF-8 デコード可能、(b) ヘッダ行が `name,phoneNumber`、
 * (c) データ行数が 1〜CSV_MAX_DATA_ROWS、(d) サイズが CSV_MAX_BYTES 以下。
 *
 * @param raw - File.arrayBuffer() で取得した生バイト列を Uint8Array に変換したもの。
 */
export function validateCsvFile(raw: Uint8Array): CsvFileValidationResult {
  const errors: string[] = [];

  if (raw.byteLength > CSV_MAX_BYTES) {
    errors.push(`ファイルサイズが上限（${CSV_MAX_BYTES.toString()} バイト）を超えています。`);
  }

  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(raw);
  } catch {
    errors.push('CSV ファイルが UTF-8 ではありません。');
    return { ok: false, errors };
  }

  // BOM を取り除いてから行分解（Excel 出力で BOM が混入することがあるため許容）。
  const stripped = text.startsWith('\uFEFF') ? text.slice(1) : text;
  const lines = stripped.split(/\r?\n/);

  // 末尾の空行は無視。
  while (lines.length > 0 && lines[lines.length - 1] === '') {
    lines.pop();
  }

  if (lines.length === 0) {
    errors.push('CSV にヘッダ行がありません。');
    return { ok: false, errors };
  }

  const header = lines[0]?.trim() ?? '';
  if (header !== CSV_REQUIRED_HEADER) {
    errors.push(
      `CSV のヘッダ行は "${CSV_REQUIRED_HEADER}" である必要があります（取得: "${header}"）。`,
    );
  }

  // データ行数：ヘッダを除き、空行を除いた数。
  let dataRowCount = 0;
  for (let i = 1; i < lines.length; i += 1) {
    const line = lines[i] ?? '';
    if (line.trim() === '') continue;
    dataRowCount += 1;
  }

  if (dataRowCount === 0) {
    errors.push('CSV にデータ行がありません（ヘッダのみ）。');
  } else if (dataRowCount > CSV_MAX_DATA_ROWS) {
    errors.push(
      `CSV のデータ行数が上限（${CSV_MAX_DATA_ROWS.toString()} 行）を超えています（取得: ${dataRowCount.toString()} 行）。`,
    );
  }

  return { ok: errors.length === 0, errors };
}

/**
 * Uint8Array を base64 文字列に変換するブラウザ向けユーティリティ。
 *
 * Lambda の API は `csvBase64` プロパティで base64 文字列を期待するため、
 * `File` から得た `ArrayBuffer` を SPA 側で base64 化する必要がある。
 * 本関数は `btoa` を用いた標準的な変換で、外部依存を追加しない。
 *
 * 大きなバッファでも一度のコンカチでメモリ効率を確保するため、
 * 8KiB ずつチャンク処理する（btoa の引数文字列長を抑える目的）。
 */
export function encodeBase64(raw: Uint8Array): string {
  const CHUNK = 0x2000; // 8 KiB
  let binary = '';
  for (let offset = 0; offset < raw.byteLength; offset += CHUNK) {
    const slice = raw.subarray(offset, offset + CHUNK);
    binary += String.fromCharCode(...slice);
  }
  return btoa(binary);
}
