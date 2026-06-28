"""Privacy / anonymization helpers (Task 15.12, Phase 15.x).

Currently exposes :func:`anonymize_employee_id` — a one-way SHA-256-based
hash function used by the EmployeeApi anonymize endpoint to scrub past
Cycle Response rows on a personal request (GDPR Right to Erasure等).
"""

from .anonymize import ANONYMIZED_ID_PREFIX, anonymize_employee_id

__all__ = ["ANONYMIZED_ID_PREFIX", "anonymize_employee_id"]
