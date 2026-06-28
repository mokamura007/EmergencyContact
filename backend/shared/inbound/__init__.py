"""Inbound (callback voice) helpers (Phase 9.2 / 9.3).

Pure functions consumed by the InboundHandler Lambda
(``backend/lambdas/inbound_handler``). The Cycle selection logic in
:mod:`shared.inbound.cycle_selection` is the target of Property 11
(see design.md / Property 11) — Phase 9.3 finalises the Hypothesis
strategy and PBT.
"""
