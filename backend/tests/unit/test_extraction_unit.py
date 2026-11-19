"""Step 2-C unit tests: the synthetic fixtures against their own PDFs, classification,
extraction (golden files), parsing and the provider.

The first group is the safeguard against a fixture that passes while the PDF is wrong: every
embedded layout line is compared with the text PDFium actually reads from the PDF at that
box, and every extracted value and label is checked the same way.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections import Counter
from datetime import date
from typing import Any

import pypdfium2 as pdfium
import pytest

from meip.core.config import AuthMode, ConfigurationError, Environment, Settings
from meip.documents.extraction import (
    EXCLUSIONS,
    SUPPORTING_MARKERS,
    Layout,
    ProviderError,
    SyntheticLayoutProvider,
    classify,
    extract,
    parse_date,
    parse_money,
)
from tests.synthetic_docs import LAYOUT_MARKER, NON_PAYSLIPS, PAYSLIPS, SyntheticDocument

ALL_DOCUMENTS = {**{f"payslip-{k}": v for k, v in PAYSLIPS.items()}, **NON_PAYSLIPS}


def _document(name: str) -> SyntheticDocument:
    made: Any = ALL_DOCUMENTS[name]()
    document: SyntheticDocument = made[0] if isinstance(made, tuple) else made
    return document


def _analyse(pdf: bytes) -> Layout:
    return asyncio.run(SyntheticLayoutProvider().analyse(pdf))


def visible_text(pdf: bytes, page_number: int, box: tuple[float, float, float, float]) -> str:
    """The text PDFium reads inside ``box`` (points, origin top-left) on the page."""
    document = pdfium.PdfDocument(pdf)
    try:
        page = document[page_number - 1]
        _, height = page.get_size()
        x0, y0, x1, y1 = box
        text = page.get_textpage().get_text_bounded(
            left=x0, bottom=height - y1, right=x1, top=height - y0
        )
        return str(text).strip()
    finally:
        document.close()


def page_characters(pdf: bytes, page_number: int) -> Counter[str]:
    document = pdfium.PdfDocument(pdf)
    try:
        text = document[page_number - 1].get_textpage().get_text_range()
        return Counter(c for c in str(text) if not c.isspace())
    finally:
        document.close()


def assert_layout_matches_pdf(pdf: bytes) -> None:
    """Every embedded line is visible at its box, and each page shows nothing else."""
    layout = _analyse(pdf)
    for line in layout.lines:
        box = (line.box.x0, line.box.y0, line.box.x1, line.box.y1)
        assert visible_text(pdf, line.page, box) == line.text, (line.page, line.text)
    for number in layout.pages:
        expected = Counter(
            c for line in layout.lines if line.page == number for c in line.text if not c.isspace()
        )
        assert page_characters(pdf, number) == expected, f"page {number} differs"


# --- the fixtures are faithful to their PDFs -------------------------------------------------


@pytest.mark.parametrize("name", sorted(ALL_DOCUMENTS))
def test_embedded_layout_matches_the_visible_pdf(name: str) -> None:
    assert_layout_matches_pdf(_document(name).pdf())


def _tampered(pdf: bytes, change: Any) -> bytes:
    start = pdf.rfind(LAYOUT_MARKER)
    raw = json.loads(base64.b64decode(pdf[start + len(LAYOUT_MARKER) :].strip()))
    change(raw)
    return pdf[:start] + LAYOUT_MARKER + base64.b64encode(json.dumps(raw).encode()) + b"\n"


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw["lines"][-1].__setitem__("text", "£9,999.99"),  # wrong value
        lambda raw: raw["lines"][-1].__setitem__(  # box moved off the text
            "box", [b + 40 if i % 2 else b for i, b in enumerate(raw["lines"][-1]["box"])]
        ),
        lambda raw: raw["lines"].pop(),  # a visible line missing from the layout
    ],
    ids=["wrong-text", "moved-box", "missing-line"],
)
def test_the_check_catches_a_layout_that_disagrees_with_the_pdf(change: Any) -> None:
    doc, _ = PAYSLIPS["classic"]()
    with pytest.raises(AssertionError):
        assert_layout_matches_pdf(_tampered(doc.pdf(), change))


# --- extraction golden files -----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PAYSLIPS))
def test_payslip_extraction_matches_ground_truth_and_the_pdf(name: str) -> None:
    doc, expected = PAYSLIPS[name]()
    pdf = doc.pdf()
    layout = _analyse(pdf)
    assert classify(layout.text).is_payslip
    result = extract(layout)

    def section(name: str) -> list[Any]:
        return [f for f in result.fields if f.section.value == name]

    assert {f.field_code: f.raw_text for f in section("summary")} == expected.summary
    assert [(f.label_text, f.raw_text, f.field_code) for f in section("payment")] == (
        expected.payments
    )
    assert [(f.label_text, f.raw_text, f.field_code) for f in section("deduction")] == (
        expected.deductions
    )
    assert {f.field_code: f.raw_text for f in section("year_to_date")} == expected.year_to_date
    assert result.missing_required == []
    assert [f.ordinal for f in result.fields] == list(range(1, len(result.fields) + 1))

    # Every extracted value and label is really shown at its evidence box in the PDF.
    for f in result.fields:
        value_box = (f.value.box.x0, f.value.box.y0, f.value.box.x1, f.value.box.y1)
        label_box = (f.label.box.x0, f.label.box.y0, f.label.box.x1, f.label.box.y1)
        assert visible_text(pdf, f.value.page, value_box) == f.raw_text
        assert visible_text(pdf, f.label.page, label_box) == f.label_text
        assert f.value.page == f.label.page


def test_amounts_and_dates_are_normalised() -> None:
    doc, _ = PAYSLIPS["earnings"]()
    fields = {f.label_text: f for f in extract(_analyse(doc.pdf())).fields}
    assert fields["Basic Salary"].money_minor == 375000
    assert fields["Unpaid Leave Adjustment"].money_minor == -12050
    assert fields["Date Paid"].value_date == date(2026, 2, 28)
    assert fields["Period Ending"].value_date == date(2026, 2, 28)
    assert fields["Tax Code"].value_text == "BR"


def test_an_unparseable_value_keeps_its_text_and_no_value() -> None:
    doc = SyntheticDocument()
    for y, (label, value) in enumerate(
        [("Pay Date", "end of month"), ("Gross Pay", "£3,250.00"), ("Net Pay", "TBC")]
    ):
        doc.text(1, 50, 100 + 15 * y, label)
        doc.right(1, 520, 100 + 15 * y, value)
    result = extract(_analyse(doc.pdf()))
    fields = {f.field_code: f for f in result.fields}
    assert fields["pay_date"].raw_text == "end of month"
    assert fields["pay_date"].value_date is None
    assert fields["net_pay"].money_minor is None
    assert result.missing_required == ["pay_date", "net_pay"]


# --- classification ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(NON_PAYSLIPS))
def test_look_alike_documents_are_not_payslips(name: str) -> None:
    assert not classify(_analyse(NON_PAYSLIPS[name]().pdf()).text).is_payslip


@pytest.mark.parametrize(
    ("text", "markers", "is_payslip"),
    [
        ("Net Pay\nGross Pay\nTax Code", ["net_pay", "gross", "tax_code"], True),
        ("Take-home pay\nPAYE\nDeductions", ["net_pay", "paye", "deductions"], True),
        ("Net Pay\nGross Pay", ["net_pay", "gross"], False),  # one supporting group
        (
            "Gross Pay\nTax Code\nPAYE\nDeductions",
            ["gross", "tax_code", "paye", "deductions"],
            False,
        ),
        (
            "net pay\nNI contributions\nperiod ending",
            ["net_pay", "national_insurance", "pay_period"],
            True,
        ),
        (
            "Net Pay\nGross Pay\nTax Code\nP60",
            ["net_pay", "gross", "tax_code", "exclusion:p60"],
            False,
        ),
        ("netpay\ngrosspay\ntaxcode", [], False),  # word boundaries
        ("planet pay", [], False),
    ],
)
def test_classification_markers(text: str, markers: list[str], is_payslip: bool) -> None:
    result = classify(text)
    assert result.markers == markers
    assert result.is_payslip is is_payslip


@pytest.mark.parametrize(
    ("group", "text"),
    [
        ("gross", "Total Earnings"),
        ("gross", "total pay"),
        ("tax_code", "TAX CODE"),
        ("paye", "PAYE ref"),
        ("national_insurance", "National Insurance"),
        ("national_insurance", "NIC"),
        ("pay_period", "Tax Week 12"),
        ("deductions", "Deductions"),
    ],
)
def test_each_supporting_marker(group: str, text: str) -> None:
    assert SUPPORTING_MARKERS[group].search(text)


@pytest.mark.parametrize(
    ("group", "text"),
    [
        ("p60", "End of Year Certificate"),
        ("p45", "Details of employee leaving work"),
        ("tax_calculation", "Tax calculation"),
        ("bank_statement", "Opening balance"),
    ],
)
def test_each_exclusion(group: str, text: str) -> None:
    assert EXCLUSIONS[group].search(text)


# --- parsing -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "pence"),
    [
        ("£3,250.00", 325000),
        ("3250.00", 325000),
        ("(12.50)", -1250),
        ("-£12.50", -1250),
        ("£0.99", 99),
        ("£1,000,000.00", 100000000),
        ("£3,25.00", None),
        ("3250", None),
        ("(12.50", None),
        ("12.5", None),
        ("£", None),
        ("", None),
    ],
)
def test_parse_money(text: str, pence: int | None) -> None:
    assert parse_money(text) == pence


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("31/01/2026", date(2026, 1, 31)),
        ("31-01-2026", date(2026, 1, 31)),
        ("31 Jan 2026", date(2026, 1, 31)),
        ("31 January 2026", date(2026, 1, 31)),
        ("31/02/2026", None),
        ("2026-01-31", None),
        ("end of month", None),
    ],
)
def test_parse_date(text: str, parsed: date | None) -> None:
    assert parse_date(text) == parsed


# --- the synthetic provider ------------------------------------------------------------------


def _with_raw(raw: Any) -> bytes:
    return b"%PDF-1.7\n" + LAYOUT_MARKER + base64.b64encode(json.dumps(raw).encode()) + b"\n"


@pytest.mark.parametrize(
    "pdf",
    [
        b"%PDF-1.7 no layout here",
        b"%PDF-1.7\n" + LAYOUT_MARKER + b"not base64!!\n",
        b"%PDF-1.7\n" + LAYOUT_MARKER + base64.b64encode(b"not json") + b"\n",
        _with_raw(
            {"source": "azure", "unit": "pt", "origin": "top-left", "pages": [], "lines": []}
        ),
        _with_raw(
            {
                "source": "synthetic-fixture",
                "unit": "pt",
                "origin": "top-left",
                "pages": [{"number": 1, "width": 100, "height": 100}],
                "lines": [{"page": 1, "text": "x", "box": [0, 0, 200, 10], "confidence": None}],
            }
        ),
    ],
    ids=["no-marker", "bad-base64", "bad-json", "not-synthetic", "box-off-page"],
)
def test_provider_failures_are_provider_errors(pdf: bytes) -> None:
    with pytest.raises(ProviderError):
        _analyse(pdf)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PRODUCTION])
def test_synthetic_provider_is_refused_when_deployed(env: Environment) -> None:
    with pytest.raises(ConfigurationError, match="synthetic"):
        Settings.model_validate(
            {
                "env": env,
                "auth_mode": AuthMode.OIDC,
                "oidc_issuer": "https://issuer.example/",
                "oidc_audience": "api",
                "oidc_jwks_url": "https://issuer.example/keys",
                "documents_enabled": True,
                "document_retention_approval": "D10-approved",
                "blob_connection_string": (
                    "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=a2V5"
                ),
            }
        )
