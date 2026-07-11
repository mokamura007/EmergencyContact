/**
 * authChallengeStore の単体テスト。
 *
 * Validates: issue #3 再々修正の `NewPasswordRequiredChallenge` ページ間受渡し。
 * DataCloneError 回避のため history.state を使わない設計の正当性を担保する。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { NewPasswordRequiredChallenge, TokenSet } from './types';

import {
  clearPendingChallenge,
  consumePendingChallenge,
  setPendingChallenge,
} from './authChallengeStore';

const dummyTokens: TokenSet = {
  idToken: 'id',
  accessToken: 'access',
  expiresAtEpochSeconds: 9_999_999_999,
};

function makeChallenge(): NewPasswordRequiredChallenge {
  return {
    kind: 'NEW_PASSWORD_REQUIRED',
    complete: vi.fn().mockResolvedValue(dummyTokens),
  };
}

describe('authChallengeStore', () => {
  beforeEach(() => {
    // 各テスト前に必ず消去（テスト間の状態漏れ防止）。
    clearPendingChallenge();
  });
  afterEach(() => {
    clearPendingChallenge();
  });

  it('未セット時は consume が null を返す', () => {
    expect(consumePendingChallenge()).toBeNull();
  });

  it('set 後の consume は同じ challenge を返す', () => {
    const challenge = makeChallenge();
    setPendingChallenge(challenge);
    expect(consumePendingChallenge()).toBe(challenge);
  });

  it('consume は 1 回のみ有効（2 回目は null）', () => {
    const challenge = makeChallenge();
    setPendingChallenge(challenge);
    expect(consumePendingChallenge()).toBe(challenge);
    expect(consumePendingChallenge()).toBeNull();
  });

  it('set の 2 回目は上書きされる', () => {
    const first = makeChallenge();
    const second = makeChallenge();
    setPendingChallenge(first);
    setPendingChallenge(second);
    expect(consumePendingChallenge()).toBe(second);
  });

  it('clearPendingChallenge は consume を伴わずに破棄する', () => {
    const challenge = makeChallenge();
    setPendingChallenge(challenge);
    clearPendingChallenge();
    expect(consumePendingChallenge()).toBeNull();
  });

  it('challenge.complete は set/consume を通しても呼び出し可能', async () => {
    const challenge = makeChallenge();
    setPendingChallenge(challenge);
    const retrieved = consumePendingChallenge();
    expect(retrieved).not.toBeNull();
    if (retrieved) {
      await expect(retrieved.complete('NewPassword1!')).resolves.toEqual(dummyTokens);
    }
  });
});
