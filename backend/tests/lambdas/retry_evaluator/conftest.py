"""Local conftest for RetryEvaluator Lambda unit tests.

The handler has no environment-variable dependencies (pure compute
Lambda — see Phase 6.5 design judgments). The file exists so pytest
treats this directory as a leaf test package and so future env-var
needs have a single landing spot.
"""

from __future__ import annotations
