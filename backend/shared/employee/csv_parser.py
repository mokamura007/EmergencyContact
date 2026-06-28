"""CSV parser + validator for Employee bulk import (Requirements 3.1-3.7, Property 7).

The parser is **pure** — it takes raw bytes and returns either a list of
valid `EmployeeRow` objects or a list of `CsvParseError` records with
line numbers. The handler then decides what to do (TransactWriteItems on
success, error response on failure).

Property 7 (target of Hypothesis PBT in Phase 13.x):
    parse_employee_csv(R) returns rows iff every data row passes the
    mandatory-fields / E.164 / no-CSV-internal-duplicate checks. On any
    failure the row list is empty and the error list enumerates every
    failing line. The existence check against the live Employee_Master
    is NOT part of this function — that happens at the DynamoDB layer
    in the handler.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from shared.employee.validate import is_valid_e164, is_valid_name

MAX_DATA_ROWS = 300
MAX_BYTES = 1 * 1024 * 1024  # 1 MiB
REQUIRED_HEADERS: tuple[str, ...] = ("name", "phoneNumber")


@dataclass(frozen=True)
class EmployeeRow:
    name: str
    phone_number: str


@dataclass(frozen=True)
class CsvParseError:
    line: int  # 1-indexed; line 1 = header, data starts at line 2. 0 = file-level error.
    reason: str


@dataclass(frozen=True)
class CsvParseResult:
    rows: list[EmployeeRow]
    errors: list[CsvParseError]


def parse_employee_csv(raw_bytes: bytes) -> CsvParseResult:
    """Parse and validate an employee CSV blob.

    Args:
        raw_bytes: The raw bytes of the uploaded CSV file.

    Returns:
        CsvParseResult. If parsing succeeded with no errors, `rows`
        holds every accepted row and `errors` is empty. If parsing
        failed (file-level error, header mismatch, or any data-row
        violation), `rows` is empty and `errors` enumerates every
        failure.
    """
    if len(raw_bytes) > MAX_BYTES:
        return CsvParseResult(
            rows=[],
            errors=[CsvParseError(line=0, reason=f"File exceeds {MAX_BYTES} bytes")],
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return CsvParseResult(
            rows=[],
            errors=[CsvParseError(line=0, reason="File is not valid UTF-8")],
        )

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return CsvParseResult(
            rows=[],
            errors=[CsvParseError(line=0, reason="Empty file (no header line)")],
        )
    except csv.Error as exc:
        # Header line malformed (stray CR / NUL / unterminated quote, etc.).
        return CsvParseResult(
            rows=[],
            errors=[CsvParseError(line=0, reason=f"CSV parse error in header: {exc}")],
        )
    header_normalized = tuple(h.strip() for h in header)
    if header_normalized != REQUIRED_HEADERS:
        return CsvParseResult(
            rows=[],
            errors=[
                CsvParseError(
                    line=1,
                    reason=f"Header must be exactly {','.join(REQUIRED_HEADERS)} (got {header_normalized})",
                )
            ],
        )

    parsed: list[EmployeeRow] = []
    errors: list[CsvParseError] = []
    seen_phones: dict[str, int] = {}  # phone_number -> first-seen line number
    data_row_count = 0

    try:
        for line_no, row in enumerate(reader, start=2):
            # Skip completely empty lines (csv.reader yields [] for blank lines).
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            data_row_count += 1
            if data_row_count > MAX_DATA_ROWS:
                errors.append(
                    CsvParseError(
                        line=line_no,
                        reason=f"Exceeds {MAX_DATA_ROWS} data rows",
                    )
                )
                break
            if len(row) != 2:
                errors.append(
                    CsvParseError(
                        line=line_no,
                        reason=f"Expected 2 columns, got {len(row)}",
                    )
                )
                continue
            name_raw = row[0].strip()
            phone_raw = row[1].strip()
            if not is_valid_name(name_raw):
                errors.append(
                    CsvParseError(line=line_no, reason="Name is empty or exceeds 100 chars")
                )
                continue
            if not is_valid_e164(phone_raw):
                errors.append(
                    CsvParseError(line=line_no, reason=f"Phone not in E.164 format: {phone_raw}")
                )
                continue
            if phone_raw in seen_phones:
                errors.append(
                    CsvParseError(
                        line=line_no,
                        reason=f"Phone duplicates line {seen_phones[phone_raw]}: {phone_raw}",
                    )
                )
                continue
            seen_phones[phone_raw] = line_no
            parsed.append(EmployeeRow(name=name_raw, phone_number=phone_raw))
    except csv.Error as exc:
        # Malformed CSV (stray CR / NUL / unterminated quotes / etc.) — keep
        # Property 7 transactionality intact by recording a file-level error
        # and returning zero rows below.
        errors.append(CsvParseError(line=0, reason=f"CSV parse error: {exc}"))

    # Property 7 (b): on any error, return zero rows.
    if errors:
        return CsvParseResult(rows=[], errors=errors)
    if not parsed:
        return CsvParseResult(
            rows=[],
            errors=[CsvParseError(line=0, reason="No data rows")],
        )
    return CsvParseResult(rows=parsed, errors=[])
