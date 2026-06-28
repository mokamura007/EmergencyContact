/**
 * 管理者ロール判定ユーティリティ。
 *
 * Cognito の ID トークン（JWT）からグループクレームを取り出し、
 * `Administrator` グループ所属かを判定する純関数群。
 *
 * 対応要件：
 *   - Requirement 1.3 : 管理者ロールが含まれる場合に管理機能を表示する。
 *   - Requirement 1.4 : 管理者ロールが含まれない場合は全機能を拒否する。
 *   - Requirement 1.9 : 一般社員ロールは存在しない（Administrator のみ判定対象）。
 *   - Property 1      : `isAuthorized(claims, F)` は `claims['cognito:groups']`
 *                       に `Administrator` が含まれるとき、かつそのときに限り true。
 *                       本モジュールでは `isAdministrator(groups)` がこの不変条件を満たす。
 *
 * 設計判断：
 *   - JWT 署名検証は API Gateway の Cognito Authorizer がサーバー側で実施する。
 *     SPA は API 呼出時に常にサーバー認可される前提のため、ここでの判定は
 *     UI 表示制御目的に限定される。最終的なセキュリティ境界の決定権を
 *     クライアント側に持たせない。
 *   - JWT の payload セグメント（base64url）のみをデコードする。署名検証
 *     はしない。これは設計判断であり、19原則(b) フォールバック禁止に
 *     対しても矛盾しない（クライアント側で「権限あり」と誤判定しても
 *     API 呼出はサーバー側で却下されるため、セキュリティ境界が破られない）。
 *   - `cognito:groups` クレームは Cognito の挙動上 (a) 配列、(b) カンマ
 *     区切り文字列、(c) クレーム自体不在、の 3 形態がある。本モジュールは
 *     ID トークン経由（通常は配列）と Access トークン経由（カンマ区切り
 *     文字列の場合あり）の双方に対応する。
 *
 * Phase 13.1 で本モジュールの `isAdministrator` を fast-check / Hypothesis
 * 相当の PBT で検証する予定（Property 1）。
 */

const ADMINISTRATOR_GROUP = 'Administrator';

/**
 * JWT payload に含まれる任意クレームを表す共有型。
 * 値の型は不明（unknown）として扱い、各アクセサ関数で型ガードする。
 */
export type JwtClaims = Readonly<Record<string, unknown>>;

/**
 * JWT の payload セグメントを base64url デコードし JSON として解釈する。
 *
 * @throws Error JWT 形式不正 / payload セグメント不在 / base64url 不正 /
 *   JSON 不正の各場合。19原則(b)：フォールバックせず原因を含めて上位へ伝播する。
 */
export function decodeJwtPayload(token: string): JwtClaims {
  if (typeof token !== 'string' || token.length === 0) {
    throw new Error('Invalid JWT: token must be a non-empty string.');
  }
  const parts = token.split('.');
  if (parts.length !== 3) {
    throw new Error(
      `Invalid JWT format: expected 3 segments separated by ".", got ${String(parts.length)}.`,
    );
  }
  const payloadSegment = parts[1];
  if (payloadSegment === undefined || payloadSegment.length === 0) {
    throw new Error('Invalid JWT format: payload segment is empty.');
  }

  // base64url → base64 変換 + パディング補完。
  const base64 = payloadSegment.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);

  let binary: string;
  try {
    binary = atob(padded);
  } catch (err) {
    throw new Error('Invalid JWT payload: not valid base64url.', { cause: err });
  }

  let parsed: unknown;
  try {
    // UTF-8 文字列として正しくデコードする。
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch (err) {
    throw new Error('Invalid JWT payload: not valid JSON.', { cause: err });
  }

  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Invalid JWT payload: expected a JSON object.');
  }

  return parsed as JwtClaims;
}

/**
 * `cognito:groups` クレームから文字列配列を抽出する。
 *
 * Cognito の挙動：
 *   - ID トークン        : 通常は `string[]`。
 *   - Access トークン    : `string` のカンマ区切り、または `string[]`。
 *   - 未所属             : クレーム自体が存在しない。
 *
 * いずれの形態にも対応するが、想定外型（オブジェクト等）は空配列を返す。
 * 19原則(b)：未知の型を勝手に文字列化するフォールバックは行わず、
 * UI 表示制御の文脈で「権限なし」側に確定的に倒す。
 */
export function extractCognitoGroups(claims: JwtClaims): readonly string[] {
  const raw = claims['cognito:groups'];
  if (Array.isArray(raw)) {
    return raw.filter((item): item is string => typeof item === 'string');
  }
  if (typeof raw === 'string') {
    return raw
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
  }
  return [];
}

/**
 * Property 1: `isAdministrator(groups)` は `groups` に `Administrator` が
 * 含まれるとき、かつそのときに限り true を返す。
 */
export function isAdministrator(groups: readonly string[]): boolean {
  return groups.includes(ADMINISTRATOR_GROUP);
}
