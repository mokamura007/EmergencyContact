"""Cognito PreSignUp Trigger — admin-only sign-up gate (Requirement 1.9).

The Cognito User Pool already has `AdminCreateUserConfig.AllowAdminCreateUserOnly=true`
(Phase 3.1), which blocks self sign-up at the Cognito API layer. This
Lambda is the Defense-in-Depth second check: if anything else were to
ever invoke the sign-up code path (e.g., a stray IdP federation
configuration), this Trigger refuses everything except the
administrator-created path.

triggerSource values handled:
    - PreSignUp_AdminCreateUser  -> ALLOW
    - PreSignUp_SignUp           -> DENY (self sign-up)
    - PreSignUp_ExternalProvider -> DENY (external IdP, SSO is out of scope)
"""

from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

ALLOWED_TRIGGER_SOURCE = "PreSignUp_AdminCreateUser"


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Cognito PreSignUp Trigger entry point.

    Raises:
        PermissionError: When the sign-up was not initiated by an
            administrator (`AdminCreateUser`). Cognito surfaces this as
            a sign-up failure.
    """
    trigger_source = event.get("triggerSource", "")
    user_name = event.get("userName", "<unknown>")

    if trigger_source != ALLOWED_TRIGGER_SOURCE:
        LOGGER.warning(
            "Sign-up denied: trigger=%s user=%s (administrator-only per Req 1.9)",
            trigger_source,
            user_name,
        )
        raise PermissionError(
            "Self sign-up is not permitted; users are created by an administrator only."
        )

    LOGGER.info("Admin-created sign-up accepted: user=%s", user_name)
    return event
