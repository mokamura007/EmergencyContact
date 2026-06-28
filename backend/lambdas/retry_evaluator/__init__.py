"""RetryEvaluator Lambda package (Phase 6.5).

Thin wrapper over :mod:`shared.retry.evaluator`. The Step Functions
``EvaluateRetry`` state invokes :func:`handler.lambda_handler` after
every dispatch attempt to decide whether to schedule another call.
"""
