"""shared.api — common API Gateway response helpers (CORS, etc.).

Introduced in Phase 15.2a CORS hotfix as a DRY-compliant home for
cross-handler concerns. The first occupant is ``cors`` which provides
the CORS header builder consumed by all six API Lambda handlers.
"""
