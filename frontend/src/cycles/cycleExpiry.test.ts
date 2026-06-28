/**
 * `cycleExpiry` の純粋関数テスト（Phase 10.7、Requirement 12.2 / 12.3）。
 */

import { describe, expect, it } from 'vitest';

import { isRetentionExpired, isWithinRetentionWindow, RETENTION_WINDOW_MS } from './cycleExpiry';

describe('isWithinRetentionWindow', () => {
  it('境界値：基準時刻から 90 日ちょうどは保管期間内（true）', () => {
    const base = new Date('2026-01-01T00:00:00Z');
    const now = new Date(base.getTime() + RETENTION_WINDOW_MS);
    expect(isWithinRetentionWindow(base.toISOString(), now)).toBe(true);
  });

  it('境界値：基準時刻から 90 日 + 1ms は保管期間外（false）', () => {
    const base = new Date('2026-01-01T00:00:00Z');
    const now = new Date(base.getTime() + RETENTION_WINDOW_MS + 1);
    expect(isWithinRetentionWindow(base.toISOString(), now)).toBe(false);
  });

  it('現在時刻が基準時刻より前なら true（未来日時は保管中扱い）', () => {
    const base = new Date('2026-06-01T00:00:00Z');
    const now = new Date('2026-01-01T00:00:00Z');
    expect(isWithinRetentionWindow(base.toISOString(), now)).toBe(true);
  });

  it('空文字は false', () => {
    expect(isWithinRetentionWindow('', new Date())).toBe(false);
  });

  it('parse 不能な文字列は false', () => {
    expect(isWithinRetentionWindow('not-iso', new Date())).toBe(false);
  });
});

describe('isRetentionExpired', () => {
  it('90 日ちょうどは未超過（false）', () => {
    const base = new Date('2026-01-01T00:00:00Z');
    const now = new Date(base.getTime() + RETENTION_WINDOW_MS);
    expect(isRetentionExpired(base.toISOString(), now)).toBe(false);
  });

  it('90 日 + 1ms は超過（true）', () => {
    const base = new Date('2026-01-01T00:00:00Z');
    const now = new Date(base.getTime() + RETENTION_WINDOW_MS + 1);
    expect(isRetentionExpired(base.toISOString(), now)).toBe(true);
  });

  it('空文字は false（押下を許してサーバー応答に委ねる）', () => {
    expect(isRetentionExpired('', new Date())).toBe(false);
  });

  it('parse 不能な文字列は false', () => {
    expect(isRetentionExpired('garbage', new Date())).toBe(false);
  });

  it('isWithinRetentionWindow と isRetentionExpired は互いに排他（有効入力）', () => {
    const base = '2026-03-01T00:00:00Z';
    const now1 = new Date('2026-03-15T00:00:00Z'); // 14 日後
    const now2 = new Date('2026-09-01T00:00:00Z'); // 184 日後
    expect(isWithinRetentionWindow(base, now1)).toBe(true);
    expect(isRetentionExpired(base, now1)).toBe(false);
    expect(isWithinRetentionWindow(base, now2)).toBe(false);
    expect(isRetentionExpired(base, now2)).toBe(true);
  });
});
