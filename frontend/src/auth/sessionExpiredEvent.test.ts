import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  _resetSessionExpiredListenersForTest,
  notifySessionExpired,
  subscribeSessionExpired,
} from './sessionExpiredEvent';

describe('sessionExpiredEvent', () => {
  afterEach(() => {
    _resetSessionExpiredListenersForTest();
    vi.restoreAllMocks();
  });

  it('notifies all subscribed listeners', () => {
    const listener1 = vi.fn();
    const listener2 = vi.fn();
    subscribeSessionExpired(listener1);
    subscribeSessionExpired(listener2);

    notifySessionExpired();

    expect(listener1).toHaveBeenCalledTimes(1);
    expect(listener2).toHaveBeenCalledTimes(1);
  });

  it('returns an unsubscribe function that stops further notifications', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeSessionExpired(listener);

    unsubscribe();
    notifySessionExpired();

    expect(listener).not.toHaveBeenCalled();
  });

  it('continues notifying other listeners when one throws', () => {
    // 例外吸収のため `console.error` をモック化（テスト出力ノイズ低減）。
    // ESLint `no-empty-function` 対策で void 評価を 1 文挟む。
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {
      /* swallow */
    });
    const goodListener = vi.fn();
    const badListener = vi.fn(() => {
      throw new Error('boom');
    });

    subscribeSessionExpired(badListener);
    subscribeSessionExpired(goodListener);

    notifySessionExpired();

    expect(badListener).toHaveBeenCalledTimes(1);
    expect(goodListener).toHaveBeenCalledTimes(1);
    expect(errorSpy).toHaveBeenCalled();
  });

  it('does not invoke listeners that were never subscribed', () => {
    const listener = vi.fn();
    notifySessionExpired();
    expect(listener).not.toHaveBeenCalled();
  });
});
