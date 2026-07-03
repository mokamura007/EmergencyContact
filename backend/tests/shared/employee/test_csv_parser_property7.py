"""Property 7 — CSV import transactionality PBT (Phase 13.7).

Validates: Requirements 3.3, 3.4, 3.6

Contract (verbatim from shared/employee/csv_parser.py):
    parse_employee_csv(R) returns rows iff every data row passes the
    mandatory-fields / E.164 / no-CSV-internal-duplicate checks. On any
    failure the row list is empty and the error list enumerates every
    failing line.

The task's Done When ("不正行 1 件以上で書込件数 0、正常時に全件書込、
`成功 + 失敗 = 試行` が常に成立") translates to a single transactionality
invariant on the pure parser:

    For every input R,
        (len(result.rows) == N  AND  result.errors == [])
      XOR
        (result.rows == []      AND  len(result.errors) >= 1)

    where N is the count of non-empty data rows in R.

This file enforces:
  (a) all-valid CSV  → rows == every input row, errors == []
  (b) any-invalid CSV → rows == [], errors != []
  (c) Transactionality invariant: rows is either complete or empty,
      never partial; rows != [] implies errors == []; errors != [] implies
      rows == [].
  (d) Header-mismatch CSV → rows == [], errors with line==1.
  (e) Oversize bytes (> 1 MiB) → rows == [], errors=[(line=0, …)].
  (f) Non-UTF-8 bytes → rows == [], errors=[(line=0, …)].
  (g) Internal phone duplicates → rows == [], errors mention "duplicates".
  (h) Row-count cap (> 300 data rows) → rows == [], errors.
  (i) Empty CSV / header-only → rows == [], errors.

The DynamoDB-side TransactWriteItems all-or-nothing semantics (the
"importCsv(rows)" wrapper) is delegated to the EmployeeApi handler
tests; this file pins the parser-layer transactionality that those
tests build on.

Plus a unit-test layer pinning enumerated edge cases:
  - empty CSV / header-only
  - one valid row
  - one valid + one invalid
  - same phone 3× → 2 duplicate errors
  - name exactly 100 chars → valid (boundary)
  - name 101 chars → invalid
  - blank lines between valid rows → blanks skipped
"""

from __future__ import annotations

import io
from typing import List, Tuple

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from shared.employee.csv_parser import (
    MAX_BYTES,
    MAX_DATA_ROWS,
    REQUIRED_HEADERS,
    CsvParseResult,
    parse_employee_csv,
)
from tests.strategies import domestic_jp_phone

# Hypothesis settings: at least 100 runs per property (task requirement).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

# ---------------------------------------------------------------------------
# Local strategies.
# ---------------------------------------------------------------------------

#: ASCII printable characters used for names.  Excludes the CSV reserved
#: characters comma / quote / CR / LF so we can build CSV bodies with a
#: trivial join (the parser uses the stdlib `csv.reader` which would also
#: handle quoting, but the strategy keeps generators readable).
_NAME_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ "

#: Strategy generating valid names (non-empty after strip, <= 100 chars).
valid_name = st.text(
    alphabet=_NAME_ALPHABET, min_size=1, max_size=100
).filter(lambda s: 0 < len(s.strip()) <= 100)


def _encode_csv(rows: List[Tuple[str, str]]) -> bytes:
    """Encode (name, phone) rows as a UTF-8 CSV blob with the canonical header."""
    buf = io.StringIO()
    import csv as _csv

    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerow(list(REQUIRED_HEADERS))
    for name, phone in rows:
        writer.writerow([name, phone])
    return buf.getvalue().encode("utf-8")


@st.composite
def valid_csv_rows(
    draw: st.DrawFn, min_rows: int = 1, max_rows: int = 50
) -> List[Tuple[str, str]]:
    """Generate 1..max_rows (name, phone) pairs with unique phones."""
    n = draw(st.integers(min_value=min_rows, max_value=max_rows))
    # Use `unique_by` to guarantee phone uniqueness across the generated list.
    pairs = draw(
        st.lists(
            st.tuples(valid_name, domestic_jp_phone),
            min_size=n,
            max_size=n,
            unique_by=lambda pair: pair[1],
        )
    )
    return pairs


@st.composite
def valid_csv_bytes(draw: st.DrawFn, max_rows: int = 50) -> Tuple[bytes, int]:
    """Generate a UTF-8 CSV blob with N valid rows; return (bytes, N)."""
    rows = draw(valid_csv_rows(min_rows=1, max_rows=max_rows))
    return _encode_csv(rows), len(rows)


# ---------------------------------------------------------------------------
# (a) all-valid CSV → rows == every input row, errors == [].
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(payload=valid_csv_bytes(max_rows=50))
def test_property7_all_valid_returns_all_rows(payload: Tuple[bytes, int]) -> None:
    """All-valid CSV → result.rows holds every input row, errors == [].

    Validates: Requirements 3.3 (全行 valid → 全行 1 トランザクションで書込)
    """
    raw, n = payload
    result = parse_employee_csv(raw)
    assert result.errors == [], (
        f"expected no errors for all-valid CSV; got {result.errors!r}"
    )
    assert len(result.rows) == n, (
        f"expected {n} rows for all-valid CSV; got {len(result.rows)}"
    )


# ---------------------------------------------------------------------------
# (b) any-invalid CSV → rows == [], errors != [].
# ---------------------------------------------------------------------------


@st.composite
def csv_with_at_least_one_invalid(draw: st.DrawFn) -> bytes:
    """Generate a CSV with at least one invalid row (mixed valid/invalid)."""
    valid_pairs = draw(valid_csv_rows(min_rows=0, max_rows=20))
    # Inject at least one invalid row.  Choose the failure mode randomly.
    failure_mode = draw(st.sampled_from(["empty_name", "bad_phone", "wrong_columns"]))
    if failure_mode == "empty_name":
        invalid_pair = ("   ", draw(domestic_jp_phone))  # whitespace-only → strip empty
    elif failure_mode == "bad_phone":
        invalid_pair = (draw(valid_name), draw(st.text(min_size=1, max_size=20).filter(
            lambda s: not (s.startswith("0") and s[1:].isdigit() and 9 <= len(s[1:]) <= 10)
        )))
    else:
        # wrong_columns: emit a one-column row.
        # We can't represent this via the (name, phone) tuple path; build the
        # bytes manually below.
        invalid_pair = None  # type: ignore[assignment]
    # Build the rows list; for wrong_columns we hand-craft the CSV.
    if failure_mode == "wrong_columns":
        buf = io.StringIO()
        import csv as _csv

        writer = _csv.writer(buf, lineterminator="\n")
        writer.writerow(list(REQUIRED_HEADERS))
        for name, phone in valid_pairs:
            writer.writerow([name, phone])
        # The hand-crafted one-column row.
        writer.writerow([draw(valid_name)])
        return buf.getvalue().encode("utf-8")
    # Shuffle the invalid row into the list at a random index.
    rows = list(valid_pairs)
    idx = draw(st.integers(min_value=0, max_value=len(rows)))
    rows.insert(idx, invalid_pair)  # type: ignore[arg-type]
    return _encode_csv(rows)


@PBT_SETTINGS
@given(raw=csv_with_at_least_one_invalid())
def test_property7_any_invalid_returns_zero_rows(raw: bytes) -> None:
    """CSV with at least one invalid row → rows == [], errors != [].

    Validates: Requirements 3.4 (1 行でも違反 → 1 件もインポートしない)
    """
    result = parse_employee_csv(raw)
    assert result.rows == [], (
        f"expected empty rows for any-invalid CSV; got {len(result.rows)} rows "
        f"(errors={result.errors!r})"
    )
    assert len(result.errors) >= 1, (
        f"expected at least one error for any-invalid CSV; got {result.errors!r}"
    )


# ---------------------------------------------------------------------------
# (c) Transactionality invariant: result is either all-success or all-failure.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(raw=st.binary(min_size=0, max_size=4096))
def test_property7_transactionality_invariant(raw: bytes) -> None:
    """For arbitrary input bytes, parse_employee_csv is all-or-nothing.

    Two transactionality clauses (Requirements 3.3 + 3.4):
        rows != []  → errors == []   (success implies no recorded failures)
        errors != [] → rows == []    (any recorded failure suppresses all rows)

    Together they form the XOR "成功 + 失敗 = 試行" property: either we got
    every parsed row with no errors, or we got zero rows with at least one
    error. Mixed results never occur.

    Validates: Requirements 3.3, 3.4, 3.6
    """
    result: CsvParseResult = parse_employee_csv(raw)
    if result.rows:
        assert result.errors == [], (
            f"transactionality broken: rows={len(result.rows)} but errors "
            f"is non-empty: {result.errors!r}"
        )
    else:
        # An empty result MUST carry at least one error explaining why.
        # (Even an empty file yields a line==0 "Empty file" or "No data rows"
        # error; there is no silent zero-row success.)
        assert len(result.errors) >= 1, (
            f"transactionality broken: rows==[] AND errors==[] for input "
            f"{raw[:80]!r} (len={len(raw)})"
        )


# ---------------------------------------------------------------------------
# (d) Header-mismatch CSV → rows == [], errors at line==1.
# ---------------------------------------------------------------------------

#: Strategy generating header bytes that do NOT equal the required header.
_bad_headers = st.sampled_from(
    [
        b"phoneNumber,name\n",  # reversed order
        b"name,phone\n",  # wrong second column name
        b"Name,phoneNumber\n",  # case mismatch on first column
        b"name,phone_number\n",  # snake_case mismatch
        b"name\n",  # missing column
        b"name,phoneNumber,extra\n",  # extra column
        b"\n",  # empty header line
    ]
)


@PBT_SETTINGS
@given(bad_header=_bad_headers)
def test_property7_bad_header_rejects_file(bad_header: bytes) -> None:
    """Any header != ("name","phoneNumber") → rows == [], errors at line==1.

    Validates: Requirements 3.5 (ファイルレベルエラー、書込ゼロ件)
    """
    body = b"alice,+8190123456789\nbob,+8190123456790\n"
    result = parse_employee_csv(bad_header + body)
    assert result.rows == []
    assert len(result.errors) >= 1
    # The first error must be the header-level error, at line 1.
    assert result.errors[0].line == 1


# ---------------------------------------------------------------------------
# (e) Oversize bytes (> 1 MiB) → file-level error at line==0.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(extra=st.integers(min_value=1, max_value=64))
def test_property7_oversize_file_rejected(extra: int) -> None:
    """raw bytes longer than MAX_BYTES → file-level error.

    Validates: Requirements 3.5 (1 MiB 超過の拒否)
    """
    raw = b"x" * (MAX_BYTES + extra)
    result = parse_employee_csv(raw)
    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].line == 0
    # The parser must NOT have attempted to UTF-8-decode an oversize blob.
    assert "exceeds" in result.errors[0].reason.lower() or "byte" in result.errors[0].reason.lower()


# ---------------------------------------------------------------------------
# (f) Non-UTF-8 bytes → file-level error at line==0.
# ---------------------------------------------------------------------------

#: Strategy generating byte sequences that are guaranteed NOT to be valid
#: UTF-8 (each starts with a continuation/lead byte invalid in isolation).
_not_utf8 = st.sampled_from(
    [
        b"\xff\xfe\x00name,phoneNumber\n",  # invalid lead byte
        b"\x80\x81\x82\x83",  # bare continuation bytes
        b"\xc0\xc1",  # always-invalid UTF-8 lead pair
        b"\xed\xa0\x80",  # high surrogate (invalid in UTF-8)
        b"\xf8\x88\x80\x80\x80",  # 5-byte sequence (invalid in UTF-8)
    ]
)


@PBT_SETTINGS
@given(raw=_not_utf8)
def test_property7_non_utf8_rejected(raw: bytes) -> None:
    """Non-UTF-8 bytes → file-level error at line==0.

    Validates: Requirements 3.5 (UTF-8 でない場合の拒否)
    """
    # Sanity: confirm the input really is not UTF-8.
    try:
        raw.decode("utf-8")
        raise AssertionError(f"generator drift: {raw!r} unexpectedly decodes as UTF-8")
    except UnicodeDecodeError:
        pass
    result = parse_employee_csv(raw)
    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].line == 0


# ---------------------------------------------------------------------------
# (g) Internal phone duplicates → rows == [], errors mention "duplicates".
# ---------------------------------------------------------------------------


@st.composite
def csv_with_duplicate_phone(draw: st.DrawFn) -> bytes:
    """Generate a CSV with the same domestic JP phone appearing on >= 2 data rows."""
    dup_phone = draw(domestic_jp_phone)
    name_a = draw(valid_name)
    name_b = draw(valid_name)
    # Optionally add unrelated valid rows before / after.
    prefix = draw(valid_csv_rows(min_rows=0, max_rows=5))
    # Ensure prefix phones don't accidentally collide with dup_phone.
    prefix = [(n, p) for n, p in prefix if p != dup_phone]
    rows = list(prefix) + [(name_a, dup_phone), (name_b, dup_phone)]
    return _encode_csv(rows)


@PBT_SETTINGS
@given(raw=csv_with_duplicate_phone())
def test_property7_internal_duplicate_phone_rejected(raw: bytes) -> None:
    """Same phone on >= 2 rows → rows == [], errors include "duplicates".

    Validates: Requirements 3.4 (CSV 内重複の検出)
    """
    result = parse_employee_csv(raw)
    assert result.rows == []
    assert any("duplicates" in e.reason.lower() for e in result.errors), (
        f"expected a 'duplicates' error in {result.errors!r}"
    )


# ---------------------------------------------------------------------------
# (h) Row-count cap (> 300 data rows) → file-level rejection.
# ---------------------------------------------------------------------------


def test_property7_row_count_cap_rejected() -> None:
    """301 valid data rows → rows == [], errors include row-cap message.

    Validates: Requirements 3.5 (300 行超過の拒否)

    Note: We use a fixed deterministic generator rather than Hypothesis here
    to keep run time bounded (301 rows × 200 examples would be wasteful).
    """
    rows = [(f"name{i:04d}", f"090{i:08d}") for i in range(MAX_DATA_ROWS + 1)]
    raw = _encode_csv(rows)
    result = parse_employee_csv(raw)
    assert result.rows == []
    assert len(result.errors) >= 1
    # The cap error must mention the row count limit.
    assert any(
        str(MAX_DATA_ROWS) in e.reason and "data rows" in e.reason.lower()
        for e in result.errors
    ), f"expected row-cap error in {result.errors!r}"


# ---------------------------------------------------------------------------
# (i) Empty CSV / header-only → file-level error.
# ---------------------------------------------------------------------------


def test_property7_empty_bytes_rejected() -> None:
    """b'' → rows == [], errors at line==0."""
    result = parse_employee_csv(b"")
    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].line == 0


def test_property7_header_only_rejected() -> None:
    """Header line only (no data rows) → rows == [], errors at line==0."""
    raw = b"name,phoneNumber\n"
    result = parse_employee_csv(raw)
    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].line == 0
    assert "no data rows" in result.errors[0].reason.lower()


# ---------------------------------------------------------------------------
# Unit examples — enumerated edge cases.
# ---------------------------------------------------------------------------


def test_unit_one_valid_row_succeeds() -> None:
    """Single valid row → rows == [the row], errors == []."""
    raw = b"name,phoneNumber\nAlice,09012345678\n"
    result = parse_employee_csv(raw)
    assert result.errors == []
    assert len(result.rows) == 1
    assert result.rows[0].name == "Alice"
    assert result.rows[0].phone_number == "+819012345678"


def test_unit_one_valid_plus_one_invalid_returns_zero_rows() -> None:
    """One valid + one invalid → rows == [] (transactionality)."""
    raw = (
        b"name,phoneNumber\n"
        b"Alice,09012345678\n"
        b"Bob,not-a-phone\n"
    )
    result = parse_employee_csv(raw)
    assert result.rows == []
    assert len(result.errors) == 1
    assert result.errors[0].line == 3  # 1=header, 2=alice valid, 3=bob invalid


def test_unit_same_phone_three_times_yields_two_duplicate_errors() -> None:
    """Same phone on lines 2/3/4 → two duplicate errors (for lines 3 and 4)."""
    raw = (
        b"name,phoneNumber\n"
        b"Alice,09012345678\n"
        b"Bob,09012345678\n"
        b"Charlie,09012345678\n"
    )
    result = parse_employee_csv(raw)
    assert result.rows == []
    duplicate_errors = [e for e in result.errors if "duplicates" in e.reason.lower()]
    assert len(duplicate_errors) == 2
    # First duplicate is line 3 (referring to line 2), second is line 4.
    assert duplicate_errors[0].line == 3
    assert "line 2" in duplicate_errors[0].reason
    assert duplicate_errors[1].line == 4
    assert "line 2" in duplicate_errors[1].reason


def test_unit_name_exactly_100_chars_is_valid() -> None:
    """100-char name → valid (inclusive upper bound)."""
    name = "a" * 100
    raw = f"name,phoneNumber\n{name},09012345678\n".encode("utf-8")
    result = parse_employee_csv(raw)
    assert result.errors == []
    assert len(result.rows) == 1
    assert result.rows[0].name == name


def test_unit_name_101_chars_is_invalid() -> None:
    """101-char name → invalid (strict upper bound)."""
    name = "a" * 101
    raw = f"name,phoneNumber\n{name},09012345678\n".encode("utf-8")
    result = parse_employee_csv(raw)
    assert result.rows == []
    assert len(result.errors) == 1
    assert "name" in result.errors[0].reason.lower()


def test_unit_blank_lines_between_valid_rows_are_skipped() -> None:
    """Blank lines between valid rows are skipped, not counted as data rows."""
    raw = (
        b"name,phoneNumber\n"
        b"Alice,09012345678\n"
        b"\n"
        b"\n"
        b"Bob,09012345679\n"
        b"\n"
    )
    result = parse_employee_csv(raw)
    assert result.errors == []
    assert len(result.rows) == 2
    assert result.rows[0].name == "Alice"
    assert result.rows[1].name == "Bob"


def test_unit_max_data_rows_boundary_exactly_300_succeeds() -> None:
    """Exactly MAX_DATA_ROWS (300) valid rows → all 300 accepted."""
    rows = [(f"name{i:04d}", f"090{i:08d}") for i in range(MAX_DATA_ROWS)]
    raw = _encode_csv(rows)
    result = parse_employee_csv(raw)
    assert result.errors == []
    assert len(result.rows) == MAX_DATA_ROWS


def test_unit_max_bytes_boundary_at_limit_accepts_decoding() -> None:
    """File exactly MAX_BYTES in size → not rejected by the size guard.

    (The content may still fail header / row checks, but the size guard
    must use a strict `>` comparison: `len > MAX_BYTES`, not `>=`.)
    """
    # Build a payload of exactly MAX_BYTES (1 MiB) using many short rows.
    # We avoid one huge field (Python csv enforces a 131_072-byte field cap
    # by default) by repeating a short two-column data row.
    header = b"name,phoneNumber\n"
    row = b"a,09012345678\n"  # 14 bytes
    body_target = MAX_BYTES - len(header)
    # Repeat the short row to fill exactly body_target bytes.  body_target
    # is divisible by len(row) only by coincidence; pad the tail with `a`s
    # appended to the last name field (still a syntactically valid CSV row).
    full_rows = body_target // len(row)
    tail = body_target - full_rows * len(row)
    # Last row gets `tail` extra padding bytes injected into the name.
    if tail > 0:
        # Drop one full row, replace with a row whose name is padded so the
        # whole replacement row's length is len(row) + tail.
        full_rows -= 1
        pad_row = b"a" + b"a" * tail + b",09012345678\n"
        body = row * full_rows + pad_row
    else:
        body = row * full_rows
    raw = header + body
    assert len(raw) == MAX_BYTES, f"setup error: built {len(raw)} bytes, expected {MAX_BYTES}"
    result = parse_employee_csv(raw)
    # The SIZE guard MUST NOT have fired at exactly MAX_BYTES.  We do not
    # care whether the result is success or row-cap rejection — both are
    # acceptable; only the file-level "exceeds" branch is forbidden here.
    for err in result.errors:
        if err.line == 0:
            assert "exceeds" not in err.reason.lower() or str(MAX_BYTES) not in err.reason, (
                f"size guard incorrectly fired at MAX_BYTES exact: {err!r}"
            )


# ---------------------------------------------------------------------------
# Explicit @example anchors — pin a few well-known cases into Hypothesis runs.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@example(raw=b"")
@example(raw=b"name,phoneNumber\n")
@example(raw=b"name,phoneNumber\nAlice,09012345678\n")
@example(raw=b"name,phoneNumber\nAlice,not-a-phone\n")
@given(raw=st.binary(min_size=0, max_size=512))
def test_property7_transactionality_examples_anchored(raw: bytes) -> None:
    """Same invariant as (c) but with explicit anchors for the regression suite."""
    result = parse_employee_csv(raw)
    if result.rows:
        assert result.errors == []
    else:
        assert len(result.errors) >= 1
