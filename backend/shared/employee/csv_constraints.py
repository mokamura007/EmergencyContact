"""CSV file-level constraint validator for Employee bulk import (Property 6, Phase 13.6).

Validates: Requirements 3.1, 3.5

This module exposes a single boolean predicate `accept_csv_file(raw_bytes)` that
returns whether a raw upload buffer satisfies *every* FILE-LEVEL precondition
required to accept an Employee CSV import. It is intentionally separate from
`parse_employee_csv` (Property 7), which performs the full per-row validation
(E.164 syntax, name length, internal duplicates, etc.).

Contract (verbatim from `accept_csv_file` docstring):

    Return True iff the buffer satisfies the FILE-LEVEL constraints
    required to accept an Employee CSV import.

    Checks (AND-condition):
        (i)   `raw_bytes` is `bytes` (not str / None / int / ...),
        (ii)  `len(raw_bytes) <= 1 MiB` (MAX_BYTES, Requirement 3.5),
        (iii) `raw_bytes` is valid UTF-8 (Requirement 3.1),
        (iv)  header line equals exactly "name,phoneNumber" (Requirement 3.5),
        (v)   data row count (non-empty lines, excluding header) is in
              the range [1, MAX_DATA_ROWS] (Requirement 3.5).

    Returns:
        True iff every check passes. False otherwise.

Use sites:
  - Lightweight gate in the EmployeeApi handler before invoking the heavier
    `parse_employee_csv` row-by-row validator.
  - SPA-side pre-flight (when the same predicate is mirrored on the
    frontend, the contract MUST remain identical).
  - API Gateway / edge-layer admission control.

DRY: constants are imported from `csv_parser` (MAX_BYTES / MAX_DATA_ROWS /
REQUIRED_HEADERS). DO NOT redefine them here — Property 6 and Property 7 MUST
agree on the same numeric limits and header tuple.
"""

from __future__ import annotations

import csv
import io

from shared.employee.csv_parser import MAX_BYTES, MAX_DATA_ROWS, REQUIRED_HEADERS

__all__ = ["accept_csv_file"]


def accept_csv_file(raw_bytes: bytes) -> bool:
    """Return True iff the buffer satisfies the FILE-LEVEL constraints
    required to accept an Employee CSV import.

    Checks (AND-condition):
        (i)   `raw_bytes` is `bytes` (not str / None / int / ...),
        (ii)  `len(raw_bytes) <= 1 MiB` (MAX_BYTES, Requirement 3.5),
        (iii) `raw_bytes` is valid UTF-8 (Requirement 3.1),
        (iv)  header line equals exactly `"name,phoneNumber"` (Requirement 3.5),
        (v)   data row count (non-empty lines, excluding header) is in
              the range [1, MAX_DATA_ROWS] (Requirement 3.5).

    Returns:
        True iff every check passes. False otherwise.

    Scope:
        This predicate validates **file-level** constraints only.
        Per-row content validation (E.164 syntax, name length, internal
        duplicates) is the job of `parse_employee_csv` (Property 7).
    """
    # (i) Type guard: bytes only.  bytearray and memoryview are excluded
    #     by design — the upload pipeline delivers `bytes` exclusively
    #     (API Gateway → Lambda payload base64-decoded to bytes).
    if type(raw_bytes) is not bytes:
        return False

    # (ii) Size cap.  Use strict `>` so the boundary value MAX_BYTES is
    #      accepted (consistent with `parse_employee_csv`).
    if len(raw_bytes) > MAX_BYTES:
        return False

    # (iii) UTF-8 validity.
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False

    # (iv) Header line.  Use csv.reader to honour the same parsing rules
    #      as `parse_employee_csv` (handles quoted fields, CRLF, etc.).
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        # Empty buffer.
        return False
    except csv.Error:
        # Malformed CSV at header line.
        return False
    if tuple(h.strip() for h in header) != REQUIRED_HEADERS:
        return False

    # (v) Data row count: non-empty lines after the header, in [1, MAX_DATA_ROWS].
    data_row_count = 0
    try:
        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue  # Skip completely-blank lines (same rule as parser).
            data_row_count += 1
            if data_row_count > MAX_DATA_ROWS:
                return False
    except csv.Error:
        return False

    return data_row_count >= 1
