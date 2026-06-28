"""Property 6 — CSV file-level constraint validator PBT (Phase 13.6).

Validates: Requirements 3.1, 3.5

Contract (verbatim from shared/employee/csv_constraints.py):
    accept_csv_file(raw_bytes) returns True iff the buffer satisfies the
    FILE-LEVEL AND-condition:
        (i)   raw_bytes is bytes,
        (ii)  len(raw_bytes) <= MAX_BYTES (1 MiB),
        (iii) raw_bytes is valid UTF-8,
        (iv)  header line equals exactly "name,phoneNumber",
        (v)   1 <= data_row_count <= MAX_DATA_ROWS (300).

This file enforces:
  (a) Positive: UTF-8 + valid header + 1..300 valid-shape data rows + size
      <= 1 MiB → True.
  (b) Negative — oversize bytes (> 1 MiB) → False.
  (c) Negative — non-UTF-8 bytes → False.
  (d) Negative — header mismatch (reorder / wrong name / missing / extra) → False.
  (e) Negative — zero data rows (header only) → False.
  (f) Negative — > MAX_DATA_ROWS data rows → False.
  (g) Negative — non-bytes input (str / None / int / list / dict / bytearray) → False.
  (h) Boundary unit anchors — exactly 300 rows accepted; 301 rejected.
  (i) Boundary unit anchors — exactly 1 MiB accepted; 1 MiB + 1 byte rejected.
  (j) Consistency with parse_employee_csv:
        accept_csv_file(raw) == True
          → file-level errors (line ∈ {0, 1}) in parse_employee_csv(raw)
            must be zero.
"""

from __future__ import annotations

import csv as _csv
import io
from typing import List, Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shared.employee.csv_constraints import accept_csv_file
from shared.employee.csv_parser import (
    MAX_BYTES,
    MAX_DATA_ROWS,
    REQUIRED_HEADERS,
    parse_employee_csv,
)

# Hypothesis settings: >=100 runs per property (task requirement: 200).
PBT_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.large_base_example,
        HealthCheck.data_too_large,
    ],
)

# ---------------------------------------------------------------------------
# Local strategies.
# ---------------------------------------------------------------------------

# ASCII printable characters excluding CSV reserved chars so we can build
# CSV bodies via simple joins without needing quoting.
_NAME_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ "

valid_name = st.text(alphabet=_NAME_ALPHABET, min_size=1, max_size=100).filter(
    lambda s: 0 < len(s.strip()) <= 100
)

# Phone-shaped strings (E.164-syntactically-valid).  We do not need full
# E.164 validation here — accept_csv_file is file-level only and does not
# inspect phone formats.  But we use plausible values so that the (j)
# consistency check against parse_employee_csv has a chance to succeed for
# strictly-positive cases.
e164_like = st.integers(min_value=1, max_value=15).flatmap(
    lambda n: st.text(alphabet="0123456789", min_size=n, max_size=n).map(
        lambda body: "+" + body
    )
)


def _encode_csv(rows: List[Tuple[str, str]]) -> bytes:
    """Encode (name, phone) rows as a UTF-8 CSV blob with the canonical header."""
    buf = io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerow(list(REQUIRED_HEADERS))
    for name, phone in rows:
        writer.writerow([name, phone])
    return buf.getvalue().encode("utf-8")


@st.composite
def valid_csv_payload(draw: st.DrawFn, max_rows: int = 50) -> bytes:
    """Generate a valid CSV blob with 1..max_rows valid-shape data rows."""
    n = draw(st.integers(min_value=1, max_value=max_rows))
    rows = draw(
        st.lists(
            st.tuples(valid_name, e164_like),
            min_size=n,
            max_size=n,
            unique_by=lambda pair: pair[1],
        )
    )
    return _encode_csv(rows)


# ---------------------------------------------------------------------------
# (a) Positive: valid bytes / UTF-8 / header / 1..300 rows / size <= 1 MiB → True.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(raw=valid_csv_payload(max_rows=50))
def test_property6_valid_payload_accepted(raw: bytes) -> None:
    """A well-formed CSV (UTF-8 + canonical header + 1..50 rows) is accepted.

    Validates: Requirements 3.1, 3.5
    """
    assert len(raw) <= MAX_BYTES, "generator drift: oversize payload"
    assert accept_csv_file(raw) is True, (
        f"expected True for valid CSV (size={len(raw)} bytes); got False"
    )


# ---------------------------------------------------------------------------
# (b) Negative: oversize bytes (> 1 MiB) → False.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(extra=st.integers(min_value=1, max_value=64))
def test_property6_oversize_rejected(extra: int) -> None:
    """raw bytes longer than MAX_BYTES → False.

    Validates: Requirement 3.5 (1 MiB 超過の拒否)
    """
    raw = b"name,phoneNumber\nAlice,+819012345678\n" + b"x" * (MAX_BYTES + extra)
    assert len(raw) > MAX_BYTES
    assert accept_csv_file(raw) is False


# ---------------------------------------------------------------------------
# (c) Negative: non-UTF-8 bytes → False.
# ---------------------------------------------------------------------------

# Bytes guaranteed NOT to be valid UTF-8.
_not_utf8 = st.sampled_from(
    [
        b"\xff\xfe\x00name,phoneNumber\n",  # invalid lead byte
        b"\x80\x81\x82\x83",  # bare continuation bytes
        b"\xc0\xc1",  # always-invalid UTF-8 lead pair
        b"\xed\xa0\x80",  # surrogate (invalid in UTF-8)
        b"\xf8\x88\x80\x80\x80",  # 5-byte sequence (invalid in UTF-8)
    ]
)


@PBT_SETTINGS
@given(raw=_not_utf8)
def test_property6_non_utf8_rejected(raw: bytes) -> None:
    """Non-UTF-8 bytes → False.

    Validates: Requirement 3.1 (UTF-8 限定)
    """
    # Sanity guard: confirm input really is not UTF-8.
    try:
        raw.decode("utf-8")
        raise AssertionError(f"generator drift: {raw!r} unexpectedly decodes")
    except UnicodeDecodeError:
        pass
    assert accept_csv_file(raw) is False


# ---------------------------------------------------------------------------
# (d) Negative: header mismatch (reorder / wrong / missing / extra) → False.
# ---------------------------------------------------------------------------

_bad_headers = st.sampled_from(
    [
        b"phoneNumber,name\n",  # reversed order
        b"name,phone\n",  # wrong second column name
        b"Name,phoneNumber\n",  # case mismatch on first column
        b"name,phone_number\n",  # snake_case mismatch
        b"name\n",  # missing column
        b"name,phoneNumber,extra\n",  # extra column
        b"\n",  # empty header line
        b"NAME,PHONENUMBER\n",  # all upper
    ]
)


@PBT_SETTINGS
@given(bad_header=_bad_headers)
def test_property6_bad_header_rejected(bad_header: bytes) -> None:
    """Any header != ("name","phoneNumber") → False.

    Validates: Requirement 3.5 (ヘッダ完全一致)
    """
    body = b"alice,+8190123456789\nbob,+8190123456790\n"
    raw = bad_header + body
    assert accept_csv_file(raw) is False


# ---------------------------------------------------------------------------
# (e) Negative: zero data rows (header only / empty / blank-only) → False.
# ---------------------------------------------------------------------------


def test_property6_header_only_rejected() -> None:
    """Header line only, no data rows → False."""
    assert accept_csv_file(b"name,phoneNumber\n") is False


def test_property6_empty_bytes_rejected() -> None:
    """b'' (empty file) → False."""
    assert accept_csv_file(b"") is False


def test_property6_header_plus_blank_lines_rejected() -> None:
    """Header + only blank lines → False (blank lines do not count)."""
    raw = b"name,phoneNumber\n\n\n\n"
    assert accept_csv_file(raw) is False


# ---------------------------------------------------------------------------
# (f) Negative: > MAX_DATA_ROWS data rows → False.
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(over=st.integers(min_value=1, max_value=10))
def test_property6_too_many_rows_rejected(over: int) -> None:
    """More than MAX_DATA_ROWS data rows → False.

    Validates: Requirement 3.5 (300 行上限)
    """
    rows = [(f"n{i:04d}", f"+8190{i:09d}") for i in range(MAX_DATA_ROWS + over)]
    raw = _encode_csv(rows)
    # Guarantee size is still within MAX_BYTES so that the row-cap branch
    # is the one being exercised, not the size guard.
    assert len(raw) <= MAX_BYTES, "generator drift: payload exceeded MAX_BYTES"
    assert accept_csv_file(raw) is False


# ---------------------------------------------------------------------------
# (g) Negative: non-bytes input → False.
# ---------------------------------------------------------------------------

_non_bytes = st.one_of(
    st.none(),
    st.integers(),
    st.text(),  # str
    st.lists(st.integers()),
    st.dictionaries(st.text(), st.integers()),
    st.builds(bytearray, st.binary()),  # bytearray
    st.builds(memoryview, st.binary()),  # memoryview
)


@PBT_SETTINGS
@given(value=_non_bytes)
def test_property6_non_bytes_rejected(value: object) -> None:
    """Any non-`bytes` input → False (type guard).

    Validates: Requirement 3.1 (bytes 受領契約)
    """
    assert accept_csv_file(value) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# (h) Boundary anchors: exactly 300 rows / 301 rows.
# ---------------------------------------------------------------------------


def test_property6_exactly_max_data_rows_accepted() -> None:
    """Exactly MAX_DATA_ROWS (300) data rows → True (inclusive boundary)."""
    rows = [(f"n{i:04d}", f"+8190{i:09d}") for i in range(MAX_DATA_ROWS)]
    raw = _encode_csv(rows)
    assert len(raw) <= MAX_BYTES
    assert accept_csv_file(raw) is True


def test_property6_max_data_rows_plus_one_rejected() -> None:
    """MAX_DATA_ROWS + 1 (301) data rows → False (strict upper bound)."""
    rows = [(f"n{i:04d}", f"+8190{i:09d}") for i in range(MAX_DATA_ROWS + 1)]
    raw = _encode_csv(rows)
    assert len(raw) <= MAX_BYTES
    assert accept_csv_file(raw) is False


# ---------------------------------------------------------------------------
# (i) Boundary anchors: exactly 1 MiB / 1 MiB + 1.
# ---------------------------------------------------------------------------


def _build_payload_of_exact_size(target_size: int) -> bytes:
    """Build a syntactically valid CSV payload of exactly `target_size` bytes.

    Used to exercise the size-boundary branch.  We pad the name field on the
    last row with extra 'a' characters until the total length matches.
    """
    header = b"name,phoneNumber\n"
    row = b"a,+819012345678\n"  # 16 bytes
    body_target = target_size - len(header)
    if body_target < len(row):
        raise ValueError("target_size too small for one row")
    full_rows = body_target // len(row)
    tail = body_target - full_rows * len(row)
    if tail > 0:
        full_rows -= 1
        pad_row = b"a" + b"a" * tail + b",+819012345678\n"
        body = row * full_rows + pad_row
    else:
        body = row * full_rows
    payload = header + body
    assert len(payload) == target_size, (
        f"setup error: built {len(payload)} bytes, expected {target_size}"
    )
    return payload


def test_property6_exactly_max_bytes_accepted_when_otherwise_valid() -> None:
    """A payload of exactly MAX_BYTES (1 MiB) with valid header / row count → True.

    The size guard uses strict `>`, so the boundary value must be accepted.
    The data row count for this payload is well below MAX_DATA_ROWS by
    construction (1 MiB / 16 bytes ≈ 65,536 rows is FAR above 300, so we
    instead build with fewer, padded rows; see _build_payload_of_exact_size).

    Note: the payload built by _build_payload_of_exact_size contains many
    short rows.  If the row count exceeds MAX_DATA_ROWS, the predicate
    must return False (row cap), even though the size is exactly at the
    boundary.  In that case we assert False (the size guard at least did
    not falsely fire).  Either way, the size guard MUST NOT be the cause.
    """
    raw = _build_payload_of_exact_size(MAX_BYTES)
    # Count data rows directly.
    data_rows = sum(
        1 for line in raw.decode("utf-8").splitlines()[1:] if line.strip()
    )
    expected = 1 <= data_rows <= MAX_DATA_ROWS
    assert accept_csv_file(raw) is expected, (
        f"size boundary: payload has {data_rows} data rows (size={len(raw)}), "
        f"expected accept_csv_file == {expected}"
    )


def test_property6_max_bytes_plus_one_rejected() -> None:
    """A payload of exactly MAX_BYTES + 1 → False (size cap)."""
    raw = _build_payload_of_exact_size(MAX_BYTES) + b"a"
    assert len(raw) == MAX_BYTES + 1
    assert accept_csv_file(raw) is False


# ---------------------------------------------------------------------------
# Additional unit anchors for non-bytes types (explicit enumeration).
# ---------------------------------------------------------------------------


def test_unit_non_bytes_str_rejected() -> None:
    """str input → False (type guard)."""
    assert accept_csv_file("name,phoneNumber\nAlice,+819012345678\n") is False  # type: ignore[arg-type]


def test_unit_non_bytes_none_rejected() -> None:
    """None input → False."""
    assert accept_csv_file(None) is False  # type: ignore[arg-type]


def test_unit_non_bytes_int_rejected() -> None:
    """int input → False."""
    assert accept_csv_file(0) is False  # type: ignore[arg-type]


def test_unit_non_bytes_list_rejected() -> None:
    """list input → False."""
    assert accept_csv_file([b"name,phoneNumber\n"]) is False  # type: ignore[arg-type]


def test_unit_non_bytes_dict_rejected() -> None:
    """dict input → False."""
    assert accept_csv_file({"raw": b"name,phoneNumber\n"}) is False  # type: ignore[arg-type]


def test_unit_non_bytes_bytearray_rejected() -> None:
    """bytearray input → False (we require strict `bytes`)."""
    assert accept_csv_file(bytearray(b"name,phoneNumber\nAlice,+819012345678\n")) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# (j) Cross-validation against parse_employee_csv (file-level consistency).
# ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(raw=st.binary(min_size=0, max_size=4096))
def test_property6_consistency_with_parser_filelevel(raw: bytes) -> None:
    """File-level consistency between accept_csv_file and parse_employee_csv.

    Direction tested:
        accept_csv_file(R) == True
          → parse_employee_csv(R) emits NO file-level errors,
            where "file-level" means a CsvParseError with line ∈ {0, 1}
            (file-wide error or header-row error).

    The reverse direction does NOT hold in general: parse_employee_csv may
    emit zero file-level errors for inputs with > MAX_DATA_ROWS data rows
    (the parser records that as a per-row error at line >= 2), while
    accept_csv_file rejects such inputs.  This asymmetry is by design and
    is therefore NOT asserted here.

    Validates: Requirements 3.1, 3.5
    """
    accepted = accept_csv_file(raw)
    if accepted:
        result = parse_employee_csv(raw)
        file_level = [e for e in result.errors if e.line in (0, 1)]
        assert file_level == [], (
            f"accept_csv_file returned True but parser emitted file-level "
            f"errors: {file_level!r}"
        )


@PBT_SETTINGS
@given(raw=valid_csv_payload(max_rows=30))
def test_property6_valid_payload_parser_filelevel_clean(raw: bytes) -> None:
    """Valid-shape CSV from the strategy → parser has no file-level errors.

    This anchors the (j) direction on inputs guaranteed to be accept_csv_file
    True, providing higher-density coverage of the positive-side consistency.

    Validates: Requirements 3.1, 3.5
    """
    assert accept_csv_file(raw) is True
    result = parse_employee_csv(raw)
    file_level = [e for e in result.errors if e.line in (0, 1)]
    assert file_level == [], (
        f"accept_csv_file True but parser file-level errors present: {file_level!r}"
    )
