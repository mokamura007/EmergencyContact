/**
 * 社員マスタ管理 UI のクライアント側バリデーション単体テスト。
 *
 * 対応：Requirements 2.1, 2.7, 3.1, 3.5
 *
 * 観点：
 *   - E.164 形式の境界条件（先頭 +、桁数 1〜15、非数字混入、空文字、非文字列）
 *   - 氏名長の境界条件（0 / 1 / MAX / MAX+1、空白のみ、非文字列）
 *   - CSV ファイル受入：UTF-8 失敗、BOM 付き、ヘッダ違反、データ行数 0/1/300/301、
 *     1 MiB 境界、CRLF 改行
 */

import { describe, expect, it } from 'vitest';

import {
  CSV_MAX_BYTES,
  CSV_MAX_DATA_ROWS,
  MAX_NAME_LENGTH,
  encodeBase64,
  isValidE164,
  isValidEmail,
  isValidName,
  validateCsvFile,
} from './validation';

const encoder = new TextEncoder();

describe('isValidE164', () => {
  it.each([
    '+1',
    '+12',
    '+819012345678',
    '+123456789012345', // 15 桁ぴったり
  ])('受理: %s', (value) => {
    expect(isValidE164(value)).toBe(true);
  });

  it.each([
    '', // 空文字
    '+', // 数字なし
    '819012345678', // 先頭 + なし
    '+0812345678a', // 英字混入
    '+8190 1234 5678', // 空白混入
    '+1234567890123456', // 16 桁
    '++81', // + が連続
    '＋8190', // 全角 +
  ])('拒否: %s', (value) => {
    expect(isValidE164(value)).toBe(false);
  });

  it.each([null, undefined, 42, {}, [], true])('文字列以外は拒否: %s', (value) => {
    expect(isValidE164(value)).toBe(false);
  });
});

describe('isValidName', () => {
  it('1 文字以上 MAX_NAME_LENGTH 文字以下を受理する', () => {
    expect(isValidName('山田太郎')).toBe(true);
    expect(isValidName('a')).toBe(true);
    expect(isValidName('あ'.repeat(MAX_NAME_LENGTH))).toBe(true);
  });

  it('空文字 / 空白のみ / MAX_NAME_LENGTH 超過は拒否', () => {
    expect(isValidName('')).toBe(false);
    expect(isValidName('   ')).toBe(false);
    expect(isValidName('a'.repeat(MAX_NAME_LENGTH + 1))).toBe(false);
  });

  it.each([null, undefined, 42, {}, [], true])('文字列以外は拒否: %s', (value) => {
    expect(isValidName(value)).toBe(false);
  });
});

describe('isValidEmail', () => {
  it.each([
    'a@b.c',
    'admin@example.com',
    'user.name@example.co.jp',
    'a+b@example.com',
    'a_b@example.com',
    '1234567890@example.com',
    'integration-test-admin@example.com',
  ])('受理: %s', (value) => {
    expect(isValidEmail(value)).toBe(true);
  });

  it.each([
    '', // 空文字
    'abc', // @ なし
    'abc@def', // ドメイン内ドットなし
    '@example.com', // local 空
    'abc@.com', // domain 空
    'abc@def.', // TLD 空
    ' abc@example.com', // 先頭空白
    'abc@example.com ', // 末尾空白
    'ab c@example.com', // local 内空白
    'abc@ex ample.com', // domain 内空白
    'a@b@example.com', // 複数 @
    '@', // @ のみ
  ])('拒否: %s', (value) => {
    expect(isValidEmail(value)).toBe(false);
  });

  it.each([null, undefined, 42, {}, [], true])('文字列以外は拒否: %s', (value) => {
    expect(isValidEmail(value)).toBe(false);
  });
});

describe('validateCsvFile', () => {
  it('正しい UTF-8 / ヘッダ / 1 データ行を受理する', () => {
    const buf = encoder.encode('name,phoneNumber\n山田太郎,+819012345678\n');
    const result = validateCsvFile(buf);
    expect(result.ok).toBe(true);
    expect(result.errors).toEqual([]);
  });

  it('BOM 付き UTF-8 でも受理する（Excel 出力対応）', () => {
    const buf = encoder.encode('\uFEFFname,phoneNumber\n田中,+1\n');
    const result = validateCsvFile(buf);
    expect(result.ok).toBe(true);
  });

  it('CRLF 改行でも正しく行数をカウントする', () => {
    const lines = ['name,phoneNumber'];
    for (let i = 0; i < 3; i += 1) {
      lines.push(`name${i.toString()},+${(80000 + i).toString()}`);
    }
    const buf = encoder.encode(lines.join('\r\n') + '\r\n');
    const result = validateCsvFile(buf);
    expect(result.ok).toBe(true);
  });

  it('UTF-8 として無効なら拒否する', () => {
    // 0xFF / 0xFE は UTF-8 のリードバイトとして無効。
    const invalid = new Uint8Array([0xff, 0xfe, 0x80]);
    const result = validateCsvFile(invalid);
    expect(result.ok).toBe(false);
    expect(result.errors.some((m) => m.includes('UTF-8'))).toBe(true);
  });

  it('ヘッダが "name,phoneNumber" 以外なら拒否する', () => {
    const buf = encoder.encode('fullname,tel\nA,+1\n');
    const result = validateCsvFile(buf);
    expect(result.ok).toBe(false);
    expect(result.errors.some((m) => m.includes('ヘッダ'))).toBe(true);
  });

  it('データ行が 0（ヘッダのみ）なら拒否する', () => {
    const buf = encoder.encode('name,phoneNumber\n');
    const result = validateCsvFile(buf);
    expect(result.ok).toBe(false);
    expect(result.errors.some((m) => m.includes('データ行'))).toBe(true);
  });

  it('CSV_MAX_DATA_ROWS をぴったり許容し、+1 で拒否する', () => {
    const headerOk = 'name,phoneNumber\n';
    const rows = (n: number) =>
      Array.from({ length: n }, (_, i) => `name${i.toString()},+${(80000 + i).toString()}`).join(
        '\n',
      );
    const ok = encoder.encode(headerOk + rows(CSV_MAX_DATA_ROWS) + '\n');
    expect(validateCsvFile(ok).ok).toBe(true);

    const over = encoder.encode(headerOk + rows(CSV_MAX_DATA_ROWS + 1) + '\n');
    const result = validateCsvFile(over);
    expect(result.ok).toBe(false);
    expect(result.errors.some((m) => m.includes('上限'))).toBe(true);
  });

  it('ファイルサイズが CSV_MAX_BYTES を超えると拒否する', () => {
    const oversize = new Uint8Array(CSV_MAX_BYTES + 1);
    const result = validateCsvFile(oversize);
    expect(result.ok).toBe(false);
    expect(result.errors.some((m) => m.includes('ファイルサイズ'))).toBe(true);
  });
});

describe('encodeBase64', () => {
  it('"hello" を正しく base64 化する', () => {
    expect(encodeBase64(encoder.encode('hello'))).toBe('aGVsbG8=');
  });

  it('CSV ヘッダ + 1 行を base64 化して TextDecoder で復号できる', () => {
    const original = 'name,phoneNumber\n山田太郎,+819012345678\n';
    const bytes = encoder.encode(original);
    const b64 = encodeBase64(bytes);
    // node の atob は globalThis にバインドされている。
    const decoded = new Uint8Array(
      atob(b64)
        .split('')
        .map((c) => c.charCodeAt(0)),
    );
    expect(new TextDecoder('utf-8').decode(decoded)).toBe(original);
  });
});
