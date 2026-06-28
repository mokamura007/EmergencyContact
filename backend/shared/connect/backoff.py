"""Exponential backoff with jitter for Connect API retries (Phase 6.2).

ConnectDispatcher (Phase 6.2) retries ``StartOutboundVoiceContact`` up
to three times on ``ThrottlingException`` / ``LimitExceededException``
(Requirements 5.1 / 5.2 / 9.6). The wait between retries is computed by
``compute_backoff_delay`` below — split into a pure function so it can
be tested without sleeping and so Phase 13.24 (Property 24: 再試行回数
上限不変条件) can drive Hypothesis property tests against it.

Design choices:
    * ``random_fn`` is dependency-injected (default :func:`random.uniform`)
      so callers / tests can pass a deterministic stub. This keeps the
      function pure with respect to its inputs.
    * The formula is::

          base * 2 ** attempt   (clamped to ``max_ms``)   ← exponential
                  * (1 + jitter)                          ← multiplicative jitter

      where ``jitter`` is drawn uniformly from
      ``[-jitter_ratio, +jitter_ratio]``. The clamp happens BEFORE the
      jitter is applied so the worst-case delay is bounded by
      ``max_ms * (1 + jitter_ratio)`` regardless of how large ``attempt``
      grows.
    * Returned value is **seconds** (float), ready to feed
      ``time.sleep`` in the production handler.

Phase 13.24 PBT candidate properties (not implemented here):
    P24a. ``compute_backoff_delay(attempt, ...) >= 0`` for all
          non-negative ``attempt``.
    P24b. ``compute_backoff_delay(attempt, ...)
              <= max_ms / 1000 * (1 + jitter_ratio)`` for all
          non-negative ``attempt``, ``base_ms`` <= ``max_ms``,
          ``jitter_ratio`` in [0, 1).
    P24c. With ``jitter_ratio = 0.0``, the result is deterministic and
          monotonically non-decreasing in ``attempt`` until the
          ``max_ms`` clamp engages, then constant.
"""

from __future__ import annotations

import random
from collections.abc import Callable

# Default jitter source. Tests inject a deterministic substitute.
_DEFAULT_RANDOM_UNIFORM: Callable[[float, float], float] = random.uniform


def compute_backoff_delay(
    attempt: int,
    base_ms: int = 200,
    max_ms: int = 5000,
    jitter_ratio: float = 0.5,
    random_fn: Callable[[float, float], float] = _DEFAULT_RANDOM_UNIFORM,
) -> float:
    """Return the number of **seconds** to wait before retry ``attempt``.

    Pure function (no I/O) given a deterministic ``random_fn``.

    Args:
        attempt: 0-based retry index. ``0`` is the wait before the first
            retry, ``1`` before the second, etc. Must be non-negative.
        base_ms: Base delay in milliseconds for ``attempt == 0`` before
            jitter is applied. Must be positive.
        max_ms: Hard ceiling on the pre-jitter delay in milliseconds.
            Once ``base_ms * 2 ** attempt`` exceeds this, the value is
            clamped before jitter multiplication.
        jitter_ratio: Half-width of the multiplicative jitter window
            expressed as a fraction of the clamped delay. ``0.0`` means
            no jitter (deterministic); ``0.5`` means ±50%. Must be in
            ``[0.0, 1.0)`` to keep the result non-negative.
        random_fn: Callable matching :func:`random.uniform` signature.
            Defaults to ``random.uniform``. Tests pass a deterministic
            stub.

    Returns:
        Delay in seconds (``float``). Always non-negative.

    Raises:
        ValueError: if any argument is out of the documented range.
    """
    if attempt < 0:
        raise ValueError(f"attempt must be >= 0; got {attempt}")
    if base_ms <= 0:
        raise ValueError(f"base_ms must be > 0; got {base_ms}")
    if max_ms < base_ms:
        raise ValueError(
            f"max_ms must be >= base_ms; got max_ms={max_ms} base_ms={base_ms}"
        )
    if not 0.0 <= jitter_ratio < 1.0:
        raise ValueError(
            f"jitter_ratio must be in [0.0, 1.0); got {jitter_ratio}"
        )

    # Exponential growth, clamped at max_ms.
    raw_ms = base_ms * (2**attempt)
    clamped_ms = min(raw_ms, max_ms)

    # Multiplicative jitter in [-jitter_ratio, +jitter_ratio].
    jitter: float = random_fn(-jitter_ratio, jitter_ratio)
    jittered_ms: float = clamped_ms * (1.0 + jitter)

    # Convert to seconds. The (1 + jitter) factor with jitter_ratio < 1.0
    # guarantees a non-negative result.
    return jittered_ms / 1000.0
