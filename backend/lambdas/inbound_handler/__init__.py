"""InboundHandler Lambda package (Phase 9.2).

Two-step Contact Flow callback handler:

* ``step = "identify"`` — Caller-ID lookup, Cycle selection, provisional
  Inbound_Contact write, ``flow`` attribute return.
* ``step = "finalize"`` — Inbound_Contact final-record write, Response
  ``callResultCodes += INBOUND`` for ``ACTIVE_CYCLE``.

See :mod:`lambdas.inbound_handler.handler` for the full contract.
"""
