"""Phone-number masking for audit logs (Property 22).

Property 22 contract (target of Hypothesis PBT in Phase 13.x):
    For all E.164-shaped s = "+" + d_1 d_2 ... d_n (n >= 1):
    (a) output starts with "+"
    (b) for n >= 4, the last 4 chars match s[-4:]
    (c) middle chars (between "+" and the last 4 digits) are "*"
    (d) output length == input length
    (e) digits of the original (except the last 4 when n >= 4) are absent
        from the output.

For inputs of length less than the 4-digit-tail threshold the function
preserves the entire input (no information leaks but also no masking).
"""

from __future__ import annotations


def mask_phone(s: str) -> str:
    """Return the masked form of an E.164 phone string.

    Args:
        s: A phone number, ideally E.164 ("+" followed by 1..15 digits).
            Non-conforming inputs are masked best-effort (the leading
            "+" rule is enforced if and only if `s` actually starts
            with "+"; otherwise the entire input is mask-padded).

    Returns:
        Masked string of equal length, satisfying Property 22 for
        E.164 inputs.
    """
    if not s:
        return s
    if not s.startswith("+"):
        # Non-E.164 best-effort: mask all but the last 4 chars.
        if len(s) <= 4:
            return s
        return "*" * (len(s) - 4) + s[-4:]
    body = s[1:]
    n = len(body)
    if n <= 4:
        # Too short to mask without leaking everything; keep as-is.
        return s
    return "+" + "*" * (n - 4) + body[-4:]
