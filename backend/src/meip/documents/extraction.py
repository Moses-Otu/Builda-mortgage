"""Payslip classification and extraction (Phase 2.0, step 2-C). Deterministic: no AI.

* :class:`LayoutProvider` is the one provider interface. The only implementation in this step
  is :class:`SyntheticLayoutProvider`, which reads a layout embedded in SYNTHETIC test PDFs
  by the test generator. It is refused outside local and test (settings validation); every
  run records the provider name, so synthetic results are always identifiable. Azure
  Document Intelligence (general layout model) can be added later as a second implementation.
* :func:`classify` applies the exact markers of ``payslip-rules/1``.
* :func:`extract` turns the layout into individual fields with their own labels, values and
  evidence (page and boxes), per schema ``payslip/1.0.0``. Values that cannot be parsed keep
  their raw text with no value; nothing is guessed.

Nothing here logs: callers log IDs and codes only, never document text.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol

from meip.core.config import ExtractionProvider, Settings

SCHEMA_VERSION = "payslip/1.0.0"
CLASSIFIER_VERSION = "payslip-rules/1"
REQUIRED_FIELDS = ("pay_date", "gross_pay", "net_pay")
_MAX_TEXT = 200


class ProviderError(Exception):
    """The provider could not produce a layout. The message is never logged or stored."""


class ProviderUnavailableError(Exception):
    """The provider couldn't be reached or is overloaded (timeouts, rate limits, server
    errors): worth retrying later. The message is a code, never document content."""


# --- layout ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def middle(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class Page:
    number: int
    width: float
    height: float


@dataclass(frozen=True)
class Line:
    page: int
    text: str
    box: Box
    confidence: float | None


@dataclass(frozen=True)
class Layout:
    unit: str
    pages: dict[int, Page]
    lines: list[Line]
    raw: dict[str, Any]

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class LayoutProvider(Protocol):
    name: str
    model_version: str

    async def analyse(self, pdf: bytes) -> Layout: ...


SYNTHETIC_MARKER = b"% MEIP-SYNTHETIC-LAYOUT "


class SyntheticLayoutProvider:
    """Test-only: returns the layout the synthetic generator embedded when it drew the PDF.

    Whoever uploads a document controls this "layout", which is why the provider is refused
    outside local and test."""

    name = "synthetic-fixture"
    model_version = "meip-synthetic/1"

    async def analyse(self, pdf: bytes) -> Layout:
        start = pdf.rfind(SYNTHETIC_MARKER)
        if start < 0:
            raise ProviderError("no synthetic layout in this document")
        encoded = pdf[start + len(SYNTHETIC_MARKER) :].split(b"\n", 1)[0].strip()
        try:
            raw = json.loads(base64.b64decode(encoded, validate=True))
            return _layout(raw)
        except (binascii.Error, ValueError, KeyError, TypeError) as exc:
            raise ProviderError("malformed synthetic layout") from exc


def _layout(raw: Any) -> Layout:
    if not isinstance(raw, dict) or raw.get("source") != "synthetic-fixture":
        raise ValueError("not a synthetic layout")
    unit = raw["unit"]
    if unit not in ("pt", "inch") or raw.get("origin") != "top-left":
        raise ValueError("unsupported coordinates")
    pages = {
        int(p["number"]): Page(int(p["number"]), float(p["width"]), float(p["height"]))
        for p in raw["pages"]
    }
    lines = []
    for item in raw["lines"]:
        page = pages[int(item["page"])]
        x0, y0, x1, y1 = (float(v) for v in item["box"])
        if not (0 <= x0 <= x1 <= page.width and 0 <= y0 <= y1 <= page.height):
            raise ValueError("box outside its page")
        confidence = item.get("confidence")
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise ValueError("confidence out of range")
        lines.append(
            Line(
                page=page.number,
                text=str(item["text"]),
                box=Box(x0, y0, x1, y1),
                confidence=None if confidence is None else float(confidence),
            )
        )
    return Layout(unit=unit, pages=pages, lines=lines, raw=raw)


def build_layout_provider(settings: Settings) -> LayoutProvider:
    if settings.extraction_provider is ExtractionProvider.SYNTHETIC:
        return SyntheticLayoutProvider()
    if settings.extraction_provider is ExtractionProvider.OPENAI:
        from meip.documents.openai_provider import OpenAILayoutProvider

        return OpenAILayoutProvider(settings)
    raise ValueError(f"unknown extraction provider {settings.extraction_provider}")


def raw_output_sha256(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- classification (payslip-rules/1) ----------------------------------------------------


def _any(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


REQUIRED_MARKER = _any(r"\bnet\s+pay\b", r"\btake[\s-]home\s+pay\b")
SUPPORTING_MARKERS: dict[str, re.Pattern[str]] = {
    "gross": _any(r"\bgross\s+pay\b", r"\btotal\s+(?:gross\s+)?pay\b", r"\btotal\s+earnings\b"),
    "tax_code": _any(r"\btax\s+code\b"),
    "paye": _any(r"\bpaye\b"),
    "national_insurance": _any(
        r"\bnational\s+insurance\b", r"\bni\s+(?:contributions?|ee|employee)\b", r"\bnic\b"
    ),
    "pay_period": _any(
        r"\bpay\s+(?:date|period)\b",
        r"\bperiod\s+end(?:ing)?\b",
        r"\btax\s+(?:period|week|month)\b",
    ),
    "deductions": _any(r"\bdeductions\b"),
}
EXCLUSIONS: dict[str, re.Pattern[str]] = {
    "p60": _any(r"\bP60\b", r"\bend\s+of\s+year\s+certificate\b"),
    "p45": _any(r"\bP45\b", r"\bdetails\s+of\s+employee\s+leaving\b"),
    "tax_calculation": _any(r"\bSA302\b", r"\btax\s+calculation\b"),
    "bank_statement": _any(r"\bstatement\s+of\s+account\b", r"\bopening\s+balance\b"),
}
MIN_SUPPORTING = 2


@dataclass(frozen=True)
class Classification:
    is_payslip: bool
    markers: list[str]
    """Names of the marker groups and exclusions found; never document text."""


def classify(text: str) -> Classification:
    found: list[str] = []
    if REQUIRED_MARKER.search(text):
        found.append("net_pay")
    supporting = [name for name, pattern in SUPPORTING_MARKERS.items() if pattern.search(text)]
    exclusions = [f"exclusion:{n}" for n, pattern in EXCLUSIONS.items() if pattern.search(text)]
    found += supporting + exclusions
    is_payslip = "net_pay" in found and len(supporting) >= MIN_SUPPORTING and not exclusions
    return Classification(is_payslip=is_payslip, markers=found)


# --- values ------------------------------------------------------------------------------

_MONEY = re.compile(
    r"^(?P<open>\()?(?P<minus>-)?£?\s?(?P<num>\d{1,3}(?:,\d{3})*|\d+)\.(?P<pence>\d{2})(?P<close>\))?$"
)
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")


def parse_money(text: str) -> int | None:
    """``£3,250.00`` -> 325000; ``(12.50)`` or ``-12.50`` -> -1250; anything else -> None."""
    match = _MONEY.match(text.strip())
    if match is None or bool(match["open"]) != bool(match["close"]):
        return None
    pence = int(match["num"].replace(",", "")) * 100 + int(match["pence"])
    return -pence if match["open"] or match["minus"] else pence


def parse_date(text: str) -> date | None:
    cleaned = " ".join(text.split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()  # noqa: DTZ007 - a calendar date
        except ValueError:
            continue
    return None


def _is_value(text: str) -> bool:
    return parse_money(text) is not None or parse_date(text) is not None


# --- extraction (payslip/1.0.0) ----------------------------------------------------------


class Section(StrEnum):
    SUMMARY = "summary"
    PAYMENT = "payment"
    DEDUCTION = "deduction"
    YEAR_TO_DATE = "year_to_date"


class ValueKind(StrEnum):
    MONEY = "money"
    DATE = "date"
    TEXT = "text"


def _label(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"^(?:{pattern})\s*:?$", re.IGNORECASE)


# code -> (label pattern, value kind, section)
SUMMARY_FIELDS: dict[str, tuple[re.Pattern[str], ValueKind, Section]] = {
    "pay_date": (_label(r"pay\s*date|date\s+paid|payment\s+date"), ValueKind.DATE, Section.SUMMARY),
    "pay_period_start": (
        _label(r"period\s+start(?:ing)?|period\s+from"),
        ValueKind.DATE,
        Section.SUMMARY,
    ),
    "pay_period_end": (
        _label(r"period\s+end(?:ing)?|period\s+to"),
        ValueKind.DATE,
        Section.SUMMARY,
    ),
    "pay_frequency": (_label(r"(?:pay\s+)?frequency"), ValueKind.TEXT, Section.SUMMARY),
    "tax_code": (_label(r"tax\s+code"), ValueKind.TEXT, Section.SUMMARY),
    "gross_pay": (
        _label(r"(?:total\s+)?gross\s+pay|total\s+earnings|total\s+payments|total\s+pay"),
        ValueKind.MONEY,
        Section.SUMMARY,
    ),
    "total_deductions": (_label(r"total\s+deductions"), ValueKind.MONEY, Section.SUMMARY),
    "net_pay": (_label(r"net\s+pay|take[\s-]home\s+pay"), ValueKind.MONEY, Section.SUMMARY),
    "ytd_gross_pay": (
        _label(r"(?:gross|taxable)\s+pay\s+to\s+date|ytd\s+gross(?:\s+pay)?|gross\s+ytd"),
        ValueKind.MONEY,
        Section.YEAR_TO_DATE,
    ),
    "ytd_income_tax": (
        _label(r"tax\s+(?:paid\s+)?to\s+date|ytd\s+tax|tax\s+ytd"),
        ValueKind.MONEY,
        Section.YEAR_TO_DATE,
    ),
}
SECTION_HEADINGS: dict[Section, re.Pattern[str]] = {
    Section.PAYMENT: _label(r"payments|earnings|pay\s+and\s+allowances"),
    Section.DEDUCTION: _label(r"deductions"),
}
# Line labels -> canonical codes (the financial model's measures). Unknown labels keep null.
PAYMENT_CODES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^basic(?:\s+(?:pay|salary))?\b|^salary\b", re.IGNORECASE), "basic_pay"),
    (re.compile(r"^overtime\b", re.IGNORECASE), "overtime"),
    (re.compile(r"^bonus\b", re.IGNORECASE), "bonus"),
    (re.compile(r"^commission\b", re.IGNORECASE), "commission"),
    (re.compile(r"\ballowance\b", re.IGNORECASE), "allowance"),
]
DEDUCTION_CODES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:paye(?:\s+tax)?|income\s+tax|tax)$", re.IGNORECASE), "income_tax"),
    (
        re.compile(
            r"^(?:employee\s+)?(?:ni|national\s+insurance)(?:\s+contributions?)?$|^ee\s+ni$",
            re.IGNORECASE,
        ),
        "national_insurance",
    ),
    (re.compile(r"^(?:employee\s+)?pension\b", re.IGNORECASE), "pension_contribution"),
    (re.compile(r"^student\s+loan\b", re.IGNORECASE), "student_loan_deduction"),
]
FIELD_CODES = frozenset(
    {*SUMMARY_FIELDS} | {code for _, code in PAYMENT_CODES} | {code for _, code in DEDUCTION_CODES}
)


@dataclass
class Field:
    section: Section
    ordinal: int
    label: Line
    value: Line
    field_code: str | None
    kind: ValueKind
    money_minor: int | None = None
    value_date: date | None = None
    value_text: str | None = None

    @property
    def raw_text(self) -> str:
        return self.value.text[:_MAX_TEXT]

    @property
    def label_text(self) -> str:
        return self.label.text[:_MAX_TEXT]


@dataclass
class Extraction:
    fields: list[Field] = field(default_factory=list)

    @property
    def missing_required(self) -> list[str]:
        parsed = {
            f.field_code
            for f in self.fields
            if f.money_minor is not None or f.value_date is not None or f.value_text
        }
        return [code for code in REQUIRED_FIELDS if code not in parsed]


def _rows(lines: list[Line]) -> list[list[Line]]:
    """Group lines whose vertical middles are within half a line height, page by page."""
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda ln: (ln.page, ln.box.middle, ln.box.x0)):
        row = rows[-1] if rows else None
        if (
            row
            and row[0].page == line.page
            and abs(row[0].box.middle - line.box.middle) <= max(row[0].box.height, 1) / 2
        ):
            row.append(line)
        else:
            rows.append([line])
    return [sorted(row, key=lambda ln: ln.box.x0) for row in rows]


rows_of = _rows  # shared with bank statement extraction (meip.documents.statements)


def _value_for(kind: ValueKind, cells: list[Line]) -> Line | None:
    """The value of a labelled row: the nearest suitable cell to the right of the label
    (for money in a table, the rightmost amount, i.e. the Amount column)."""
    if not cells:
        return None
    if kind is ValueKind.MONEY:
        money = [c for c in cells if parse_money(c.text) is not None]
        return money[-1] if money else cells[-1]
    return cells[0]


def _field(
    section: Section, ordinal: int, label: Line, value: Line, code: str | None, kind: ValueKind
) -> Field:
    result = Field(section, ordinal, label, value, code, kind)
    if kind is ValueKind.MONEY:
        result.money_minor = parse_money(value.text)
    elif kind is ValueKind.DATE:
        result.value_date = parse_date(value.text)
    else:
        result.value_text = value.text.strip()[:64] or None
    return result


def _code(label: str, table: list[tuple[re.Pattern[str], str]]) -> str | None:
    text = label.strip()
    return next((code for pattern, code in table if pattern.search(text)), None)


def extract(layout: Layout) -> Extraction:
    result = Extraction()
    section: Section | None = None
    seen: set[str] = set()
    for row in _rows(layout.lines):
        label, cells = row[0], row[1:]
        text = label.text.strip()
        heading = next((s for s, p in SECTION_HEADINGS.items() if p.match(text)), None)
        if heading is not None and not any(parse_money(c.text) is not None for c in cells):
            section = heading
            continue
        summary = next((c for c, (p, _, _) in SUMMARY_FIELDS.items() if p.match(text)), None)
        if summary is not None:
            _, kind, field_section = SUMMARY_FIELDS[summary]
            value = _value_for(kind, cells)
            if summary not in seen and value is not None:
                seen.add(summary)
                result.fields.append(
                    _field(field_section, len(result.fields) + 1, label, value, summary, kind)
                )
            if summary in ("gross_pay", "total_deductions"):
                section = None  # a section's total row ends it
            continue
        if section in (Section.PAYMENT, Section.DEDUCTION) and not _is_value(text):
            money = [c for c in cells if parse_money(c.text) is not None]
            if money:
                table = PAYMENT_CODES if section is Section.PAYMENT else DEDUCTION_CODES
                result.fields.append(
                    _field(
                        section,
                        len(result.fields) + 1,
                        label,
                        money[-1],
                        _code(text, table),
                        ValueKind.MONEY,
                    )
                )
    return result
