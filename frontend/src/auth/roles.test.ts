/**
 * 管理者ロール判定純関数（`auth/roles.ts`）の単体テスト。
 *
 * Validates: Requirements 1.3, 1.4, 1.9, Property 1
 *
 * Property 1: `isAdministrator(groups)` は `groups` に `Administrator` が
 * 含まれるとき、かつそのときに限り true を返す。
 *
 * 本ファイルでは表形式の境界ケース + 多角的なネガティブケースで両方向の
 * 含意（含む⇒true、含まない⇒false）を確認し、Phase 13 で Hypothesis 系
 * PBT による全数チェックに繋ぐ素地を作る。
 */

import { describe, expect, it } from 'vitest';

import { decodeJwtPayload, extractCognitoGroups, isAdministrator, type JwtClaims } from './roles';

/**
 * テスト用：任意の payload オブジェクトを base64url JSON にエンコードして
 * `header.payload.signature` 形式の擬似 JWT 文字列を作る。
 * 署名は検証対象外なので固定文字列を置く。
 */
function buildJwt(payload: Record<string, unknown>): string {
  const header = { alg: 'RS256', typ: 'JWT' };
  const encode = (obj: Record<string, unknown>): string => {
    const json = JSON.stringify(obj);
    // UTF-8 安全な base64url 変換。
    const bytes = new TextEncoder().encode(json);
    let binary = '';
    for (const b of bytes) {
      binary += String.fromCharCode(b);
    }
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };
  return `${encode(header)}.${encode(payload)}.signature`;
}

describe('decodeJwtPayload', () => {
  it('Cognito ID トークン形式の payload を JwtClaims としてデコードできる', () => {
    const token = buildJwt({
      sub: '00000000-0000-0000-0000-000000000001',
      'cognito:groups': ['Administrator'],
      email: 'admin@example.com',
      exp: 1_900_000_000,
    });
    const claims = decodeJwtPayload(token);
    expect(claims.sub).toBe('00000000-0000-0000-0000-000000000001');
    expect(claims['cognito:groups']).toEqual(['Administrator']);
  });

  it('UTF-8 マルチバイト文字（日本語名前など）を含む payload を正しく復号する', () => {
    const token = buildJwt({ name: '山田 太郎', 'cognito:groups': ['Administrator'] });
    const claims = decodeJwtPayload(token);
    expect(claims.name).toBe('山田 太郎');
  });

  it('セグメント数が 3 ではない場合は Error を投げる', () => {
    expect(() => decodeJwtPayload('aaa.bbb')).toThrow(/expected 3 segments/);
    expect(() => decodeJwtPayload('aaa.bbb.ccc.ddd')).toThrow(/expected 3 segments/);
  });

  it('空文字 / 非文字列入力では Error を投げる', () => {
    expect(() => decodeJwtPayload('')).toThrow(/non-empty string/);
    // @ts-expect-error 意図的に不正な型を渡す
    expect(() => decodeJwtPayload(null)).toThrow(/non-empty string/);
  });

  it('payload セグメントが空の場合は Error を投げる', () => {
    expect(() => decodeJwtPayload('header..signature')).toThrow(/payload segment is empty/);
  });

  it('base64url として不正な payload は Error を投げる', () => {
    // `*` は base64url にも base64 にも含まれない文字。
    expect(() => decodeJwtPayload('header.****.signature')).toThrow(/base64url/);
  });

  it('JSON として不正な payload は Error を投げる', () => {
    // "not-json-just-string" を base64url エンコードしたもの（JSON 値だが
    // オブジェクトではない文字列リテラル）。
    const notObject = btoa('"just a string"')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
    expect(() => decodeJwtPayload(`header.${notObject}.sig`)).toThrow(/expected a JSON object/);
  });
});

describe('extractCognitoGroups', () => {
  it('ID トークン形式（配列）の `cognito:groups` を返す', () => {
    const claims: JwtClaims = { 'cognito:groups': ['Administrator', 'Auditor'] };
    expect(extractCognitoGroups(claims)).toEqual(['Administrator', 'Auditor']);
  });

  it('Access トークン形式（カンマ区切り文字列）の `cognito:groups` を分割して返す', () => {
    const claims: JwtClaims = { 'cognito:groups': 'Administrator, Auditor ,  ' };
    expect(extractCognitoGroups(claims)).toEqual(['Administrator', 'Auditor']);
  });

  it('クレーム不在の場合は空配列', () => {
    expect(extractCognitoGroups({})).toEqual([]);
  });

  it('想定外の型（オブジェクト / 数値 / null）は空配列に倒す', () => {
    expect(extractCognitoGroups({ 'cognito:groups': { Administrator: true } })).toEqual([]);
    expect(extractCognitoGroups({ 'cognito:groups': 42 })).toEqual([]);
    expect(extractCognitoGroups({ 'cognito:groups': null })).toEqual([]);
  });

  it('配列内の非文字列要素は除外する', () => {
    const claims: JwtClaims = { 'cognito:groups': ['Administrator', 42, null, 'Reviewer'] };
    expect(extractCognitoGroups(claims)).toEqual(['Administrator', 'Reviewer']);
  });
});

describe('isAdministrator (Property 1)', () => {
  // Property 1: groups に 'Administrator' を含む ⇔ true を返す。
  // 両方向の含意を境界例で確認する。

  it('単独 `Administrator` を含む配列で true', () => {
    expect(isAdministrator(['Administrator'])).toBe(true);
  });

  it('他グループと併存する `Administrator` で true', () => {
    expect(isAdministrator(['Auditor', 'Administrator', 'Reviewer'])).toBe(true);
  });

  it('空配列で false', () => {
    expect(isAdministrator([])).toBe(false);
  });

  it('`Administrator` を含まない任意のグループで false', () => {
    expect(isAdministrator(['Auditor'])).toBe(false);
    expect(isAdministrator(['Employee'])).toBe(false);
    expect(isAdministrator(['admin'])).toBe(false); // 大小区別あり
    expect(isAdministrator(['ADMINISTRATOR'])).toBe(false);
    expect(isAdministrator(['Administrators'])).toBe(false); // 末尾 s
  });

  it('Requirement 1.9: 一般社員ロールは存在しないため `Employee` が含まれていても false', () => {
    expect(isAdministrator(['Employee'])).toBe(false);
    expect(isAdministrator(['Employee', 'Reviewer'])).toBe(false);
  });

  // Property 1 の合成：decodeJwtPayload → extractCognitoGroups → isAdministrator
  // という想定パイプラインを通して end-to-end の不変条件を確認する。
  it('JWT パイプライン経由：`Administrator` を含む ID トークンは管理者判定 true', () => {
    const token = buildJwt({ 'cognito:groups': ['Administrator'] });
    const groups = extractCognitoGroups(decodeJwtPayload(token));
    expect(isAdministrator(groups)).toBe(true);
  });

  it('JWT パイプライン経由：`cognito:groups` 不在の ID トークンは管理者判定 false', () => {
    const token = buildJwt({ sub: 'user-without-group' });
    const groups = extractCognitoGroups(decodeJwtPayload(token));
    expect(isAdministrator(groups)).toBe(false);
  });
});
