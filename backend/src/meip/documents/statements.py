"""Bank statement classification and extraction (Stage 2-B, step 2B-1). Deterministic: no AI.

Schema ``bank_statement/1.0.0``, classifier ``statement-rules/1``. Works on the same
:class:`~meip.documents.extraction.Layout` as payslips (the provider interface is shared).

**Provenance.** Every value keeps its own raw text, page and box: the statement summary as
``extracted_fields`` (label and value boxes), and each transaction row as cells (date, every
description line, money in, money out, running balance), each with its own box.

**Privacy.** The full account number and sort code are read **only in memory**, to compute the
keyed account fingerprint. They are never returned for storage: the ``account_ending`` field
keeps only the last four digits, and its raw text is masked. (The original PDF and the raw
provider output still contain them; see the Stage 2-B plan, section 2.)

**Refuse rather than guess.** More than one account number, a foreign currency, or no
recognisable transactions table gives a refusal reason and no rows. A row that can't be read
is kept as ``unparsed`` with its raw text and box, never dropped.

Nothing here logs: callers log IDs and codes only, never document text.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from meip.documents.extraction import (
    Box,
    Field,
    Layout,
    Line,
    Section,
    ValueKind,
    parse_date,
    parse_money,
    rows_of,
)

SCHEMA_VERSION = "bank_statement/1.0.0"
CLASSIFIER_VERSION = "statement-rules/1"
REQUIRED_FIELDS = (
    "statement_period_start",
    "statement_period_end",
    "opening_balance",
    "closing_balance",
)
DESCRIPTION_MAX = 500


def _any(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def _exact(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"^(?:{pattern})\s*:?$", re.IGNORECASE)


STATEMENT_MARKER = _any(
    r"\bstatement\s+of\s+account\b", r"\bopening\s+balance\b", r"\bbalance\s+brought\s+forward\b"
)

# Summary labels -> (field code, kind). Sort code is read only for the fingerprint.
SUMMARY_LABELS: dict[str, tuple[re.Pattern[str], ValueKind]] = {
    "account_ending": (_exact(r"account\s+(?:number|no\.?)"), ValueKind.TEXT),
    "statement_period": (_exact(r"statement\s+period|period"), ValueKind.TEXT),
    "opening_balance": (_exact(r"opening\s+balance"), ValueKind.MONEY),
    "closing_balance": (_exact(r"closing\s+balance"), ValueKind.MONEY),
    "total_paid_in": (_exact(r"total\s+(?:paid\s+in|money\s+in|credits)"), ValueKind.MONEY),
    "total_paid_out": (_exact(r"total\s+(?:paid\s+out|money\s+out|debits)"), ValueKind.MONEY),
}
SORT_CODE_LABEL = _exact(r"sort\s+code")
FIELD_CODES = frozenset(
    {
        "account_ending",
        "statement_period_start",
        "statement_period_end",
        "opening_balance",
        "closing_balance",
        "total_paid_in",
        "total_paid_out",
    }
)

HEADER_COLUMNS: dict[str, re.Pattern[str]] = {
    "date": _exact(r"date"),
    "description": _exact(r"description|details|transaction(?:s|\s+details)?"),
    "paid_out": re.compile(
        r"^(?:paid\s+out|money\s+out|debits?|payments|withdrawals)\b", re.IGNORECASE
    ),
    "paid_in": re.compile(r"^(?:paid\s+in|money\s+in|credits?|receipts|deposits)\b", re.IGNORECASE),
    "balance": re.compile(r"^balance\b", re.IGNORECASE),
}
FOREIGN_CURRENCY = re.compile(r"\$|€|\bUSD\b|\bEUR\b|\bUS\s+dollars?\b", re.IGNORECASE)
BROUGHT_FORWARD = _any(r"\bbalance\s+brought\s+forward\b", r"\bbrought\s+forward\b")
CARRIED_FORWARD = _any(r"\bbalance\s+carried\s+forward\b", r"\bcarried\s+forward\b")
_MONEYISH = re.compile(r"^-?[£$€(]?-?[\dO,.]*\d[\dO,.]*\)?(?:\s*(?:OD|DR|CR))?$", re.IGNORECASE)
_SHORT_DATES = ("%d %b", "%d %B", "%d/%m", "%d-%m")
_MONEY_ALIGN = 15.0  # pt: a money cell's right edge is within this of its column's header


class Refusal(StrEnum):
    MULTIPLE_ACCOUNTS = "statement_multiple_accounts"
    FOREIGN_CURRENCY = "statement_foreign_currency"
    NO_TABLE = "statement_no_table"


class RowKind(StrEnum):
    TRANSACTION = "transaction"
    BROUGHT_FORWARD = "brought_forward"
    CARRIED_FORWARD = "carried_forward"
    UNPARSED = "unparsed"


@dataclass(frozen=True)
class Cell:
    text: str
    page: int
    box: Box

    def as_json(self) -> dict[str, Any]:
        b = self.box
        return {"text": self.text, "page": self.page, "box": [b.x0, b.y0, b.x1, b.y1]}


@dataclass
class TransactionRow:
    row_number: int
    kind: RowKind
    page: int
    posted_on: date | None = None
    date_inherited: bool = False
    paid_in: int | None = None
    paid_out: int | None = None
    balance: int | None = None
    date_cell: Cell | None = None
    description_cells: list[Cell] = field(default_factory=list)
    paid_in_cell: Cell | None = None
    paid_out_cell: Cell | None = None
    balance_cell: Cell | None = None
    other_cells: list[Cell] = field(default_factory=list)

    @property
    def description_full(self) -> str:
        return "\n".join(c.text for c in self.description_cells)

    @property
    def description(self) -> str:
        return self.description_full[:DESCRIPTION_MAX]

    @property
    def description_truncated(self) -> bool:
        return len(self.description_full) > DESCRIPTION_MAX

    @property
    def cells(self) -> list[Cell]:
        found = [self.date_cell, *self.description_cells, self.paid_out_cell, self.paid_in_cell]
        found += [self.balance_cell, *self.other_cells]
        return [c for c in found if c is not None]

    @property
    def row_box(self) -> Box:
        same_page = [c.box for c in self.cells if c.page == self.page]
        return Box(
            min(b.x0 for b in same_page),
            min(b.y0 for b in same_page),
            max(b.x1 for b in same_page),
            max(b.y1 for b in same_page),
        )

    def cells_json(self) -> dict[str, Any]:
        """Each value's own raw text, page and box (the ``cells`` column)."""

        def one(c: Cell | None) -> dict[str, Any] | None:
            return c.as_json() if c is not None else None

        return {
            "date": one(self.date_cell),
            "date_inherited": self.date_inherited,
            "description": [c.as_json() for c in self.description_cells],
            "paid_out": one(self.paid_out_cell),
            "paid_in": one(self.paid_in_cell),
            "balance": one(self.balance_cell),
            "other": [c.as_json() for c in self.other_cells],
        }


@dataclass
class StatementExtraction:
    is_statement: bool
    markers: list[str]
    fields: list[Field] = field(default_factory=list)
    rows: list[TransactionRow] = field(default_factory=list)
    refusal: Refusal | None = None
    # In memory only, for the fingerprint: never stored.
    account_number: str | None = None
    sort_code: str | None = None

    @property
    def has_balance_column(self) -> bool:
        return any(r.balance_cell is not None for r in self.rows)


@dataclass(frozen=True)
class _Columns:
    page: int
    date_x0: float
    description_x0: float
    money: dict[str, float]  # column -> header right edge

    @property
    def money_left(self) -> float:
        return min(self.money.values()) - 80.0


def _header(cells: list[Line]) -> dict[str, Line] | None:
    found: dict[str, Line] = {}
    for c in cells:
        for name, pattern in HEADER_COLUMNS.items():
            if name not in found and pattern.match(c.text.strip()):
                found[name] = c
                break
    if {"date", "description", "paid_in", "paid_out"} <= set(found):
        return found
    return None


def classify(layout: Layout) -> tuple[bool, list[str]]:
    """``statement-rules/1``: a statement marker and a transactions header."""
    markers: list[str] = []
    if STATEMENT_MARKER.search(layout.text):
        markers.append("statement")
    if any(_header(row) for row in rows_of(layout.lines)):
        markers.append("transactions_header")
    return markers == ["statement", "transactions_header"], markers


_SUFFIX = re.compile(r"^(?P<amount>.*?)\s*(?P<suffix>OD|DR|CR)$", re.IGNORECASE)


def statement_money(text: str) -> int | None:
    """Money as statements print it: ``1,200.00``, ``£1,200.00``, ``-£500.00``, ``£-500.00``,
    ``(500.00)``, and overdrawn balances with ``OD`` or ``DR`` (``CR`` is in credit)."""
    cleaned = text.strip()
    sign = 1
    suffix = _SUFFIX.match(cleaned)
    if suffix is not None:
        cleaned = suffix["amount"]
        sign = -1 if suffix["suffix"].upper() in ("OD", "DR") else 1
    cleaned = cleaned.replace("£-", "-£").replace("£", "", 1) if "£" in cleaned else cleaned
    amount = parse_money(cleaned)
    if amount is None:
        return None
    return -abs(amount) if sign < 0 else amount


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _masked(text: str) -> bool:
    return bool(re.search(r"[*xX•]", text))


def _short_date(text: str, period: tuple[date | None, date | None]) -> date | None:
    """A table date, often printed without a year: the year comes from the statement
    period (whichever of its years puts the date inside the period)."""
    full = parse_date(text)
    if full is not None:
        return full
    cleaned = " ".join(text.split())
    for fmt in _SHORT_DATES:
        try:
            parsed = datetime.strptime(f"{cleaned} 2000", f"{fmt} %Y")  # noqa: DTZ007 - a date
        except ValueError:
            continue
        start, end = period
        years = sorted({d.year for d in (start, end) if d is not None})
        for year in years:
            try:
                candidate = parsed.replace(year=year).date()
            except ValueError:
                continue
            if (start is None or candidate >= start) and (end is None or candidate <= end):
                return candidate
        return None
    return None


def extract_statement(layout: Layout) -> StatementExtraction:
    is_statement, markers = classify(layout)
    result = StatementExtraction(is_statement=is_statement, markers=markers)
    if not is_statement:
        if "statement" in markers:
            result.refusal = Refusal.NO_TABLE
        return result
    if FOREIGN_CURRENCY.search(layout.text):
        result.refusal = Refusal.FOREIGN_CURRENCY
        return result

    rows = rows_of(layout.lines)
    accounts: set[str] = set()
    period: tuple[date | None, date | None] = (None, None)
    seen: set[str] = set()

    def add_field(code: str, label: Line, value: Line, kind: ValueKind, val: Any) -> None:
        f = Field(Section.SUMMARY, len(result.fields) + 1, label, value, code, kind)
        if kind is ValueKind.MONEY:
            f.money_minor = val
        elif kind is ValueKind.DATE:
            f.value_date = val
        else:
            f.value_text = val
        result.fields.append(f)

    # Summary first (labels anywhere outside the table).
    for row in rows:
        label, cells = row[0], row[1:]
        text = label.text.strip()
        if SORT_CODE_LABEL.match(text) and cells:
            digits = _digits(cells[0].text)
            if len(digits) == 6 and not _masked(cells[0].text):
                result.sort_code = digits
            continue
        code = next((c for c, (p, _) in SUMMARY_LABELS.items() if p.match(text)), None)
        if code is None or not cells:
            continue
        value = cells[0]
        if code == "account_ending":
            digits = _digits(value.text)
            accounts.add(value.text.strip())
            if code in seen or len(digits) < 4:
                continue
            seen.add(code)
            last4 = digits[-4:]
            if not _masked(value.text) and 6 <= len(digits) <= 10:
                result.account_number = digits
            # Never store the printed number: the raw text keeps only the last four digits.
            shown = Line(value.page, f"****{last4}", value.box, value.confidence)
            add_field(code, label, shown, ValueKind.TEXT, last4)
        elif code == "statement_period":
            if code in seen:
                continue
            seen.add(code)
            parts = re.split(r"\s+(?:to|-|\u2013)\s+", value.text.strip(), maxsplit=1)
            start = parse_date(parts[0]) if parts else None
            end = parse_date(parts[1]) if len(parts) > 1 else None
            period = (start, end)
            add_field("statement_period_start", label, value, ValueKind.DATE, start)
            add_field("statement_period_end", label, value, ValueKind.DATE, end)
        elif code not in seen:
            seen.add(code)
            add_field(code, label, value, ValueKind.MONEY, statement_money(value.text))
    if len(accounts) > 1:
        result.refusal = Refusal.MULTIPLE_ACCOUNTS
        result.fields, result.account_number, result.sort_code = [], None, None
        return result

    # The transactions table, page by page (each page repeats its header).
    columns: _Columns | None = None
    last_date: date | None = None
    previous: TransactionRow | None = None
    previous_bottom = 0.0
    for row in rows:
        header = _header(row)
        if header is not None:
            columns = _Columns(
                page=row[0].page,
                date_x0=header["date"].box.x0,
                description_x0=header["description"].box.x0,
                money={
                    name: header[name].box.x1
                    for name in ("paid_out", "paid_in", "balance")
                    if name in header
                },
            )
            previous = None
            continue
        if columns is None or row[0].page != columns.page:
            continue
        label_text = row[0].text.strip()
        if any(p.match(label_text) for p, _ in SUMMARY_LABELS.values()) or SORT_CODE_LABEL.match(
            label_text
        ):
            continue  # a summary row printed below the table
        parsed = _table_row(row, columns, period, len(result.rows) + 1)
        top = min(c.box.y0 for c in row)
        if parsed is None:
            # A description continuation: no date or money, starting in the description
            # column, directly below the previous row.
            if (
                previous is not None
                and abs(row[0].box.x0 - columns.description_x0) < 5
                and top - previous_bottom < row[0].box.height * 1.5
            ):
                previous.description_cells.extend(Cell(c.text, c.page, c.box) for c in row)
                previous_bottom = max(c.box.y1 for c in row)
            continue
        if parsed.kind is RowKind.TRANSACTION:
            if parsed.posted_on is None and parsed.date_cell is None:
                parsed.posted_on, parsed.date_inherited = last_date, last_date is not None
            last_date = parsed.posted_on or last_date
        result.rows.append(parsed)
        previous, previous_bottom = parsed, max(c.box.y1 for c in row)
    if not result.rows:
        result.refusal = Refusal.NO_TABLE
    return result


def _table_row(
    row: list[Line], columns: _Columns, period: tuple[date | None, date | None], number: int
) -> TransactionRow | None:
    out = TransactionRow(row_number=number, kind=RowKind.TRANSACTION, page=row[0].page)
    unreadable = False
    for line in row:
        cell = Cell(line.text.strip(), line.page, line.box)
        text = cell.text
        if line.box.x1 < columns.description_x0 - 2:
            out.date_cell = cell
            out.posted_on = _short_date(text, period)
            unreadable |= out.posted_on is None
            continue
        if _MONEYISH.match(text) and line.box.x0 >= columns.money_left:
            name = min(columns.money, key=lambda n: abs(columns.money[n] - line.box.x1))
            if abs(columns.money[name] - line.box.x1) <= _MONEY_ALIGN:
                amount = statement_money(text)
                unreadable |= amount is None
                if name == "paid_in":
                    out.paid_in_cell, out.paid_in = cell, amount
                elif name == "paid_out":
                    out.paid_out_cell, out.paid_out = cell, amount
                else:
                    out.balance_cell, out.balance = cell, amount
                continue
            out.other_cells.append(cell)
            unreadable = True
            continue
        out.description_cells.append(cell)
    description = out.description_full
    has_money = any(c is not None for c in (out.paid_in_cell, out.paid_out_cell, out.balance_cell))
    if BROUGHT_FORWARD.search(description):
        out.kind = RowKind.BROUGHT_FORWARD
    elif CARRIED_FORWARD.search(description):
        out.kind = RowKind.CARRIED_FORWARD
    elif out.date_cell is None and not has_money:
        return None  # a continuation line (or something outside the table)
    elif unreadable or (out.paid_in_cell is None and out.paid_out_cell is None):
        out.kind = RowKind.UNPARSED
    if out.kind is RowKind.UNPARSED:
        out.paid_in = out.paid_out = out.balance = None
    return out


# --- mathematical validation (pure; shown at confirmation, reused by bank-checks/1) ---------

BALANCE_ALLOWANCE = 1  # pence


class Check(StrEnum):
    PASSED = "passed"
    DISCREPANCY = "discrepancy"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class SimpleRow:
    """A stored or extracted row reduced to what the balance checks need."""

    row_number: int
    kind: str
    paid_in: int | None
    paid_out: int | None
    balance: int | None
    posted_on: date | None = None


@dataclass(frozen=True)
class Integrity:
    """Whether the statement's numbers are internally consistent. This is **validation**,
    separate from the broker's confirmation that the extraction matches the document."""

    totals: Check
    totals_message: str
    running: Check
    running_message: str
    totals_difference: int | None = None
    break_row: int | None = None
    break_difference: int | None = None

    def codes(self) -> dict[str, str]:
        return {"totals": self.totals.value, "running": self.running.value}


def _gbp(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    pounds, pence = divmod(abs(minor), 100)
    return f"{sign}£{pounds:,}.{pence:02d}"


def integrity(
    opening: int | None,
    closing: int | None,
    rows: list[SimpleRow],
    *,
    total_in: int | None = None,
    total_out: int | None = None,
) -> Integrity:
    """``totals``: opening + money in - money out = closing (and the printed totals, when
    given, match the rows). ``running``: each printed running balance follows from the
    previous printed balance and the rows between. A statement without running balances
    makes the row-by-row check **not available**, never failed. Any unreadable row makes
    both checks not run: they can't be relied on."""
    unparsed = [r.row_number for r in rows if r.kind == "unparsed"]
    if unparsed:
        reason = (
            f"row {unparsed[0]} couldn't be read, so the balances can't be relied on"
            if len(unparsed) == 1
            else f"{len(unparsed)} rows couldn't be read, so the balances can't be relied on"
        )
        return Integrity(Check.NOT_RUN, reason, Check.NOT_RUN, reason)

    txns = [r for r in rows if r.kind == "transaction"]
    paid_in = sum(r.paid_in or 0 for r in txns)
    paid_out = sum(r.paid_out or 0 for r in txns)

    if opening is None or closing is None:
        totals, totals_msg, diff = (
            Check.NOT_RUN,
            "the opening or closing balance is missing",
            None,
        )
    else:
        expected = opening + paid_in - paid_out
        diff = closing - expected
        problems = []
        if abs(diff) > BALANCE_ALLOWANCE:
            problems.append(
                f"opening {_gbp(opening)} + money in {_gbp(paid_in)} - money out "
                f"{_gbp(paid_out)} is {_gbp(expected)}, but the closing balance is "
                f"{_gbp(closing)} ({_gbp(abs(diff))} unaccounted for)"
            )
        if total_in is not None and abs(total_in - paid_in) > BALANCE_ALLOWANCE:
            problems.append(
                f"the printed total paid in is {_gbp(total_in)}; the rows add to {_gbp(paid_in)}"
            )
        if total_out is not None and abs(total_out - paid_out) > BALANCE_ALLOWANCE:
            problems.append(
                f"the printed total paid out is {_gbp(total_out)}; the rows add to {_gbp(paid_out)}"
            )
        if problems:
            totals, totals_msg = Check.DISCREPANCY, "; ".join(problems)
        else:
            totals = Check.PASSED
            totals_msg = f"opening {_gbp(opening)} + money in - money out = closing {_gbp(closing)}"

    printed = [r for r in rows if r.balance is not None]
    if not printed:
        return Integrity(
            totals,
            totals_msg,
            Check.NOT_RUN,
            "this statement doesn't show running balances, so the row-by-row check isn't "
            "available; the totals are checked instead",
            totals_difference=diff,
        )
    running: int | None = opening
    for r in rows:
        if r.kind == "transaction" and running is not None:
            running += (r.paid_in or 0) - (r.paid_out or 0)
        if r.balance is None:
            continue
        if running is not None and abs(r.balance - running) > BALANCE_ALLOWANCE:
            gap = r.balance - running
            when = f" ({r.posted_on:%d %b})" if r.posted_on else ""
            return Integrity(
                totals,
                totals_msg,
                Check.DISCREPANCY,
                f"the running balance breaks at row {r.row_number}{when}: it shows "
                f"{_gbp(r.balance)}, but the rows before it give {_gbp(running)} "
                f"({_gbp(abs(gap))} unaccounted for: a row may be missing or misread)",
                totals_difference=diff,
                break_row=r.row_number,
                break_difference=gap,
            )
        running = r.balance
    return Integrity(
        totals,
        totals_msg,
        Check.PASSED,
        f"every printed running balance follows from the rows before it ({len(printed)} balances)",
        totals_difference=diff,
    )


def account_fingerprint(
    key: bytes, sort_code: str | None, account_number: str | None
) -> str | None:
    """HMAC-SHA256 of the sort code and account number under a server-side key: tells
    accounts apart without storing them (a plain hash of 14 digits could be reversed by
    trying every number). None when either isn't printed in full."""
    if not sort_code or not account_number:
        return None
    message = f"{sort_code}:{account_number}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()
