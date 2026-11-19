"""SYNTHETIC test documents: UK-style payslips and look-alike non-payslips.

Never real borrower data. Each document is drawn with reportlab from a list of positioned text
runs; the generator records where every run was drawn (PDF points, origin top-left) and
embeds that layout in the PDF for the synthetic layout provider. ``assert_layout_matches_pdf``
(in the tests) reads the visible text back out of the PDF at those coordinates, so the
embedded layout can never silently disagree with what the PDF shows.

Every document carries a visible "SYNTHETIC TEST DOCUMENT" line with a unique ID, so uploads
never collide as duplicates.
"""

from __future__ import annotations

import base64
import calendar
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import timedelta
from typing import Any

from reportlab.pdfbase.pdfmetrics import getAscentDescent, stringWidth
from reportlab.pdfgen import canvas

A4 = (595.0, 842.0)
FONT = "Helvetica"
SIZE = 10.0
LAYOUT_MARKER = b"% MEIP-SYNTHETIC-LAYOUT "


@dataclass
class Run:
    page: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float | None = None


@dataclass
class SyntheticDocument:
    """Positioned text runs on A4 pages. ``y`` is the distance from the top of the page to
    the text's baseline."""

    pages: int = 1
    runs: list[Run] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        self.text(1, 40, 30, f"SYNTHETIC TEST DOCUMENT {self.id}")

    def text(
        self, page: int, x: float, y: float, value: str, confidence: float | None = None
    ) -> Run:
        width = stringWidth(value, FONT, SIZE)
        ascent, descent = getAscentDescent(FONT, SIZE)
        run = Run(page, value, x, y - ascent, x + width, y - descent, confidence)
        self.runs.append(run)
        return run

    def right(self, page: int, x_right: float, y: float, value: str) -> Run:
        return self.text(page, x_right - stringWidth(value, FONT, SIZE), y, value)

    def layout(self) -> dict[str, Any]:
        return {
            "source": "synthetic-fixture",
            "generator": "meip-synthetic/1",
            "unit": "pt",
            "origin": "top-left",
            "pages": [
                {"number": n, "width": A4[0], "height": A4[1]} for n in range(1, self.pages + 1)
            ],
            "lines": [
                {
                    "page": r.page,
                    "text": r.text,
                    "box": [r.x0, r.y0, r.x1, r.y1],
                    "confidence": r.confidence,
                }
                for r in self.runs
            ],
        }

    def pdf(self, *, embed_layout: bool = True) -> bytes:
        buffer = io.BytesIO()
        drawing = canvas.Canvas(buffer, pagesize=A4)
        drawing.setTitle("Synthetic test document")
        for page in range(1, self.pages + 1):
            drawing.setFont(FONT, SIZE)
            ascent, _ = getAscentDescent(FONT, SIZE)
            for run in (r for r in self.runs if r.page == page):
                baseline_from_top = run.y0 + ascent
                drawing.drawString(run.x0, A4[1] - baseline_from_top, run.text)
            drawing.showPage()
        drawing.save()
        data = buffer.getvalue()
        if embed_layout:
            encoded = base64.b64encode(json.dumps(self.layout()).encode())
            data += b"\n" + LAYOUT_MARKER + encoded + b"\n"
        return data


# --- ground truth ------------------------------------------------------------------------


@dataclass
class Expected:
    """What extraction must produce: summary (code -> raw text) and lines
    (label, raw text, field code) per section."""

    summary: dict[str, str] = field(default_factory=dict)
    payments: list[tuple[str, str, str | None]] = field(default_factory=list)
    deductions: list[tuple[str, str, str | None]] = field(default_factory=list)
    year_to_date: dict[str, str] = field(default_factory=dict)


def _row(
    doc: SyntheticDocument, page: int, y: float, label: str, value: str, x_value: float = 520
) -> None:
    doc.text(page, 50, y, label)
    doc.right(page, x_value, y, value)


def payslip_classic() -> tuple[SyntheticDocument, Expected]:
    """Labels on the left, a units/rate/amount table, monthly, slash dates."""
    doc = SyntheticDocument()
    doc.text(1, 50, 60, "ACME WIDGETS LTD")
    doc.text(1, 50, 75, "PAYSLIP")
    _row(doc, 1, 100, "Pay Date", "31/01/2026")
    _row(doc, 1, 115, "Tax Code", "1257L")
    _row(doc, 1, 130, "Pay Frequency", "Monthly")
    _row(doc, 1, 145, "Period Start", "01/01/2026")
    _row(doc, 1, 160, "Period End", "31/01/2026")
    doc.text(1, 50, 190, "Payments")
    doc.text(1, 300, 190, "Units")
    doc.text(1, 380, 190, "Rate")
    doc.right(1, 520, 190, "Amount")
    _row(doc, 1, 205, "Basic Pay", "£2,916.67")
    doc.right(1, 330, 220, "10.00")
    doc.right(1, 420, 220, "£25.00")
    _row(doc, 1, 220, "Overtime 1.5x", "£250.00")
    _row(doc, 1, 235, "Car Allowance", "£150.00")
    _row(doc, 1, 250, "Total Gross Pay", "£3,316.67")
    doc.text(1, 50, 280, "Deductions")
    _row(doc, 1, 295, "PAYE Tax", "£454.60")
    _row(doc, 1, 310, "Employee NI", "£165.28")
    _row(doc, 1, 325, "Employee Pension", "£165.83")
    _row(doc, 1, 340, "Student Loan", "£79.00")
    _row(doc, 1, 355, "Total Deductions", "£864.71")
    _row(doc, 1, 385, "Net Pay", "£2,451.96")
    _row(doc, 1, 415, "Gross Pay To Date", "£33,166.70")
    _row(doc, 1, 430, "Tax Paid To Date", "£4,546.00")
    return doc, Expected(
        summary={
            "pay_date": "31/01/2026",
            "tax_code": "1257L",
            "pay_frequency": "Monthly",
            "pay_period_start": "01/01/2026",
            "pay_period_end": "31/01/2026",
            "gross_pay": "£3,316.67",
            "total_deductions": "£864.71",
            "net_pay": "£2,451.96",
        },
        payments=[
            ("Basic Pay", "£2,916.67", "basic_pay"),
            ("Overtime 1.5x", "£250.00", "overtime"),
            ("Car Allowance", "£150.00", "allowance"),
        ],
        deductions=[
            ("PAYE Tax", "£454.60", "income_tax"),
            ("Employee NI", "£165.28", "national_insurance"),
            ("Employee Pension", "£165.83", "pension_contribution"),
            ("Student Loan", "£79.00", "student_loan_deduction"),
        ],
        year_to_date={"ytd_gross_pay": "£33,166.70", "ytd_income_tax": "£4,546.00"},
    )


def payslip_earnings() -> tuple[SyntheticDocument, Expected]:
    """Different wording: Earnings, Basic Salary, Bonus, NI, Take Home Pay, written dates."""
    doc = SyntheticDocument()
    doc.text(1, 50, 60, "Northwind Services plc")
    _row(doc, 1, 90, "Date Paid", "28 Feb 2026", 300)
    _row(doc, 1, 105, "Tax Period", "11", 300)
    _row(doc, 1, 120, "Period Ending", "28 February 2026", 300)
    _row(doc, 1, 135, "Tax Code", "BR", 300)
    doc.text(1, 50, 165, "Earnings")
    _row(doc, 1, 180, "Basic Salary", "3,750.00")
    _row(doc, 1, 195, "Bonus", "500.00")
    _row(doc, 1, 210, "Unpaid Leave Adjustment", "(120.50)")
    _row(doc, 1, 225, "Total Earnings", "4,129.50")
    doc.text(1, 50, 255, "Deductions")
    _row(doc, 1, 270, "Income Tax", "825.90")
    _row(doc, 1, 285, "National Insurance", "247.77")
    _row(doc, 1, 300, "Pension", "206.48")
    _row(doc, 1, 315, "Total Deductions", "1,280.15")
    _row(doc, 1, 345, "Take Home Pay", "2,849.35")
    return doc, Expected(
        summary={
            "pay_date": "28 Feb 2026",
            "pay_period_end": "28 February 2026",
            "tax_code": "BR",
            "gross_pay": "4,129.50",
            "total_deductions": "1,280.15",
            "net_pay": "2,849.35",
        },
        payments=[
            ("Basic Salary", "3,750.00", "basic_pay"),
            ("Bonus", "500.00", "bonus"),
            ("Unpaid Leave Adjustment", "(120.50)", None),
        ],
        deductions=[
            ("Income Tax", "825.90", "income_tax"),
            ("National Insurance", "247.77", "national_insurance"),
            ("Pension", "206.48", "pension_contribution"),
        ],
    )


def payslip_two_pages() -> tuple[SyntheticDocument, Expected]:
    """Payments on page 1; deductions, net pay and year to date on page 2; weekly."""
    doc = SyntheticDocument(pages=2)
    doc.text(1, 50, 60, "Contoso Logistics")
    _row(doc, 1, 90, "Payment Date", "06-03-2026")
    _row(doc, 1, 105, "Frequency", "Weekly")
    doc.text(1, 50, 135, "Pay and Allowances")
    _row(doc, 1, 150, "Basic Pay", "£620.00")
    _row(doc, 1, 165, "Overtime", "£93.00")
    _row(doc, 1, 180, "Commission", "£75.00")
    _row(doc, 1, 195, "Total Payments", "£788.00")
    doc.text(2, 50, 60, "Deductions")
    _row(doc, 2, 75, "Tax", "£101.40")
    _row(doc, 2, 90, "NI Contributions", "£39.36")
    _row(doc, 2, 105, "Total Deductions", "£140.76")
    _row(doc, 2, 135, "Net Pay", "£647.24")
    _row(doc, 2, 165, "YTD Gross", "£35,460.00")
    _row(doc, 2, 180, "YTD Tax", "£4,563.00")
    return doc, Expected(
        summary={
            "pay_date": "06-03-2026",
            "pay_frequency": "Weekly",
            "gross_pay": "£788.00",
            "total_deductions": "£140.76",
            "net_pay": "£647.24",
        },
        payments=[
            ("Basic Pay", "£620.00", "basic_pay"),
            ("Overtime", "£93.00", "overtime"),
            ("Commission", "£75.00", "commission"),
        ],
        deductions=[
            ("Tax", "£101.40", "income_tax"),
            ("NI Contributions", "£39.36", "national_insurance"),
        ],
        year_to_date={"ytd_gross_pay": "£35,460.00", "ytd_income_tax": "£4,563.00"},
    )


PAYSLIPS = {
    "classic": payslip_classic,
    "earnings": payslip_earnings,
    "two-pages": payslip_two_pages,
}


# --- a consistent monthly series, for reconciliation (Stage 2-A) ---------------------------

SERIES_FAULTS = frozenset(
    {
        "missing_month",  # the payslip at ``fault_at`` is not produced (pay continues)
        "ytd_break",  # gross pay to date jumps by an extra £375.00 from ``fault_at`` on
        "arithmetic_error",  # net pay at ``fault_at`` is £10.00 too high
        "duplicate_period",  # the payslip at ``fault_at`` is produced twice
        "adjustment",  # an "Arrears Adjustment" line at ``fault_at``; YTD gross jumps by £150 more
        "taxable_label",  # every payslip says "Taxable Pay To Date" instead of gross
        "period_end_only",  # no "Period Start" row on any payslip
        "no_basic",  # the basic line is labelled "Contractual Pay" (not recognised as basic)
        "part_period",  # an "Unpaid Leave" line on the payslip at ``fault_at``
    }
)


@dataclass
class SeriesPayslip:
    document: SyntheticDocument
    period_start: date_type
    period_end: date_type
    pay_date: date_type
    gross_minor: int
    net_minor: int
    ytd_gross_minor: int
    ytd_tax_minor: int


def _money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    pounds, pence = divmod(abs(minor), 100)
    return f"{sign}£{pounds:,}.{pence:02d}"


def _month_end(year: int, month: int) -> date_type:
    return date_type(year, month, calendar.monthrange(year, month)[1])


def _last_weekday(day: date_type) -> date_type:
    while day.weekday() >= 5:  # pay moves to the Friday when the month ends at a weekend
        day -= timedelta(days=1)
    return day


def payslip_series(
    start: tuple[int, int] = (2026, 5),
    months: int = 4,
    basic_minor: int = 375_000,
    *,
    faults: frozenset[str] = frozenset(),
    fault_at: int = 2,
    positions: dict[str, int] | None = None,
) -> list[SeriesPayslip]:
    """SYNTHETIC consecutive monthly payslips with consistent year-to-date totals.

    Periods run from the 1st to the month end; pay is on the last weekday. Tax, NI and pension
    are simple made-up rates (this is test data, not a payroll engine). Year to date assumes
    the same pay in earlier months of the tax year and restarts each April. ``faults``
    (from :data:`SERIES_FAULTS`) apply at the index ``fault_at`` where they name one, or at
    their own index in ``positions`` (e.g. ``{"ytd_break": 1, "missing_month": 3}``)."""
    positions = positions or {}
    unknown = (faults | set(positions)) - SERIES_FAULTS
    faults = faults | frozenset(positions)

    def hit(fault: str, i: int) -> bool:
        return fault in faults and i == positions.get(fault, fault_at)

    if unknown:
        raise ValueError(f"Unknown faults: {sorted(unknown)}")
    year, month = start
    tax_month = (month - 4) % 12 + 1  # April is month 1 of the UK tax year
    regular_tax = max(0, basic_minor - 104_750) * 20 // 100
    ytd_gross = basic_minor * (tax_month - 1)
    ytd_tax = regular_tax * (tax_month - 1)
    extra_ytd = 0
    out: list[SeriesPayslip] = []
    for i in range(months):
        if month == 4:
            ytd_gross, ytd_tax, extra_ytd = 0, 0, 0
        period_start = date_type(year, month, 1)
        period_end = _month_end(year, month)
        pay_date = _last_weekday(period_end)
        lines: list[tuple[str, int]] = [
            ("Contractual Pay" if "no_basic" in faults else "Basic Pay", basic_minor)
        ]
        if hit("adjustment", i):
            lines.append(("Arrears Adjustment", 20_000))
            extra_ytd += 15_000
        if hit("part_period", i):
            lines.append(("Unpaid Leave", -50_000))
        if hit("ytd_break", i):
            extra_ytd += 37_500
        gross = sum(amount for _, amount in lines)
        tax = max(0, gross - 104_750) * 20 // 100
        ni = max(0, gross - 104_800) * 8 // 100
        pension = gross * 5 // 100
        deductions = tax + ni + pension
        net = gross - deductions + (1_000 if hit("arithmetic_error", i) else 0)
        ytd_gross += gross
        ytd_tax += tax

        copies = 0 if hit("missing_month", i) else 1
        if hit("duplicate_period", i):
            copies = 2
        for _ in range(copies):
            doc = SyntheticDocument()
            doc.text(1, 50, 60, "SYNTHETIC SERIES EMPLOYER LTD")
            doc.text(1, 50, 75, "PAYSLIP")
            fmt = "%d/%m/%Y"
            _row(doc, 1, 100, "Pay Date", pay_date.strftime(fmt))
            _row(doc, 1, 115, "Tax Code", "1257L")
            _row(doc, 1, 130, "Pay Frequency", "Monthly")
            if "period_end_only" not in faults:
                _row(doc, 1, 145, "Period Start", period_start.strftime(fmt))
            _row(doc, 1, 160, "Period End", period_end.strftime(fmt))
            doc.text(1, 50, 190, "Payments")
            y = 205.0
            for label, amount in lines:
                _row(doc, 1, y, label, _money(amount))
                y += 15
            _row(doc, 1, y, "Total Gross Pay", _money(gross))
            y += 30
            doc.text(1, 50, y, "Deductions")
            y += 15
            for label, amount in (
                ("PAYE Tax", tax),
                ("Employee NI", ni),
                ("Employee Pension", pension),
            ):
                _row(doc, 1, y, label, _money(amount))
                y += 15
            _row(doc, 1, y, "Total Deductions", _money(deductions))
            y += 30
            _row(doc, 1, y, "Net Pay", _money(net))
            y += 30
            ytd_label = "Taxable Pay To Date" if "taxable_label" in faults else "Gross Pay To Date"
            _row(doc, 1, y, ytd_label, _money(ytd_gross + extra_ytd))
            _row(doc, 1, y + 15, "Tax Paid To Date", _money(ytd_tax))
            out.append(
                SeriesPayslip(
                    doc,
                    period_start,
                    period_end,
                    pay_date,
                    gross,
                    net,
                    ytd_gross + extra_ytd,
                    ytd_tax,
                )
            )
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


# --- bank statements, consistent with a payslip series (Stage 2-B) --------------------------

STATEMENT_FAULTS = frozenset(
    {
        "missing_credit",  # the salary at ``fault_at`` never arrives
        "short_credit",  # it arrives £100.00 short
        "near_credit",  # it arrives £1.50 short
        "duplicate_amount_credit",  # a second credit of the same amount, 2 days earlier
        "late_credit",  # it arrives 4 days after the pay date (still within the window)
        "outside_window",  # it arrives 7 days after the pay date
        "missing_row",  # a non-salary row of statement ``fault_at`` is left out of the PDF
        "unparsed_row",  # a row's amount is printed unreadably (a letter O for a zero)
        "statement_gap",  # statement ``fault_at`` isn't produced
        "multiple_accounts",  # statement ``fault_at`` prints a second account number
        "foreign_currency",  # statement ``fault_at`` has amounts in US dollars
    }
)
STATEMENT_LAYOUTS = frozenset({"standard", "end_of_day", "no_balance", "two_pages"})
SALARY_DESCRIPTION = ("SERIES EMPLOYER LTD", "SALARY")


@dataclass
class StatementRow:
    """Ground truth for one printed row: ``kind`` is transaction, brought_forward or
    carried_forward; ``balance`` is what the row prints (None when it prints none)."""

    kind: str
    posted_on: date_type | None
    description: list[str]
    paid_in: int | None
    paid_out: int | None
    balance: int | None
    unparsed: bool = False


@dataclass
class SeriesStatement:
    document: SyntheticDocument
    account_number: str | None
    sort_code: str | None
    period_start: date_type
    period_end: date_type
    opening_minor: int
    closing_minor: int
    rows: list[StatementRow]


@dataclass
class _Txn:
    posted_on: date_type
    description: list[str]
    amount: int  # signed: credits positive
    salary: bool = False


def _next_working_day(day: date_type) -> date_type:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _plain(minor: int) -> str:
    """Table money: no £ sign, as banks print it."""
    sign = "-" if minor < 0 else ""
    pounds, pence = divmod(abs(minor), 100)
    return f"{sign}{pounds:,}.{pence:02d}"


def salaries_from(payslips: list[SeriesPayslip]) -> list[tuple[date_type, int]]:
    """(pay date, net pay) for each payslip: what the account should receive."""
    return [(p.pay_date, p.net_minor) for p in payslips]


def bank_statement_series(
    salaries: list[tuple[date_type, int]],
    *,
    start: tuple[int, int] = (2026, 5),
    months: int = 4,
    sort_code: str = "12-34-56",
    account_number: str = "12344821",
    holders: str = "MR A EXAMPLE",
    opening_minor: int = 120_000,
    layout: str = "standard",
    masked_number: bool = False,
    faults: frozenset[str] = frozenset(),
    fault_at: int = 1,
    positions: dict[str, int] | None = None,
) -> list[SeriesStatement]:
    """SYNTHETIC monthly current-account statements (never real data).

    The salary credits arrive as given (from :func:`salaries_from`); other made-up credits
    and debits fill each month, with running balances. Statements run from the 1st to the
    month end. ``fault_at`` (or ``positions``) is a month index for statement faults and an
    index into ``salaries`` for credit faults."""
    positions = positions or {}
    unknown = (faults | set(positions)) - STATEMENT_FAULTS
    if unknown or layout not in STATEMENT_LAYOUTS:
        raise ValueError(f"Unknown faults or layout: {sorted(unknown)} {layout}")
    faults = faults | frozenset(positions)

    def hit(fault: str, i: int) -> bool:
        return fault in faults and i == positions.get(fault, fault_at)

    txns: list[_Txn] = []
    for i, (pay_date, net) in enumerate(salaries):
        if hit("missing_credit", i):
            continue
        amount = net - (10_000 if hit("short_credit", i) else 150 if hit("near_credit", i) else 0)
        day = pay_date + timedelta(
            days=4 if hit("late_credit", i) else 7 if hit("outside_window", i) else 0
        )
        txns.append(_Txn(day, list(SALARY_DESCRIPTION), amount, salary=True))
        if hit("duplicate_amount_credit", i):
            txns.append(_Txn(pay_date - timedelta(days=2), ["TRANSFER FROM J SAMPLE"], net))

    year, month = start
    statements: list[SeriesStatement] = []
    balance = opening_minor
    for m in range(months):
        first = date_type(year, month, 1)
        last = _month_end(year, month)
        month_txns = [t for t in txns if first <= t.posted_on <= last] + [
            _Txn(date_type(year, month, 1), ["STANDING ORDER", "LANDLORD RENT"], -95_000),
            _Txn(date_type(year, month, 5), ["CARD PAYMENT GROCER", "LONDON GB"], -5_420),
            _Txn(date_type(year, month, 5), ["CARD PAYMENT CAFE"], -380),
            _Txn(date_type(year, month, 12), ["DIRECT DEBIT ENERGY CO"], -8_900),
            _Txn(date_type(year, month, 20), ["TRANSFER FROM S SYNTHETIC"], 2_000),
        ]
        month_txns.sort(key=lambda t: (t.posted_on, not t.salary, t.description))
        opening = balance
        rows: list[StatementRow] = [
            StatementRow("brought_forward", None, ["BALANCE BROUGHT FORWARD"], None, None, opening)
        ]
        for n, t in enumerate(month_txns):
            balance += t.amount
            same_day_next = n + 1 < len(month_txns) and month_txns[n + 1].posted_on == t.posted_on
            printed_balance: int | None = balance
            if layout == "no_balance" or (layout == "end_of_day" and same_day_next):
                printed_balance = None
            rows.append(
                StatementRow(
                    "transaction",
                    t.posted_on,
                    t.description,
                    t.amount if t.amount > 0 else None,
                    -t.amount if t.amount < 0 else None,
                    printed_balance,
                )
            )
        closing = balance
        if layout == "no_balance":
            rows[0].balance = None  # brought forward printed only in the summary
        if hit("statement_gap", m):
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            continue
        printed = list(rows)
        if hit("missing_row", m):
            printed.remove(next(r for r in printed if r.description[0] == "CARD PAYMENT CAFE"))
        if hit("unparsed_row", m):
            target = next(r for r in printed if r.description[0] == "DIRECT DEBIT ENERGY CO")
            target.unparsed = True
        doc = _draw_statement(
            printed,
            period=(first, last),
            opening=opening,
            closing=closing,
            sort_code=sort_code,
            account_number=account_number,
            holders=holders,
            masked=masked_number,
            balance_column=layout != "no_balance",
            two_pages=layout == "two_pages",
            second_account=hit("multiple_accounts", m),
            dollars=hit("foreign_currency", m),
        )
        statements.append(
            SeriesStatement(
                doc,
                None if masked_number else account_number,
                None if masked_number else sort_code,
                first,
                last,
                opening,
                closing,
                printed,
            )
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return statements


# Table columns: date at the left, description, then three right-aligned money columns.
_COL_DATE, _COL_DESC = 50.0, 110.0
_RIGHT_OUT, _RIGHT_IN, _RIGHT_BAL = 400.0, 470.0, 545.0
_TABLE_TOP, _ROW_STEP, _PAGE_BOTTOM = 330.0, 15.0, 790.0


def _draw_statement(
    rows: list[StatementRow],
    *,
    period: tuple[date_type, date_type],
    opening: int,
    closing: int,
    sort_code: str,
    account_number: str,
    holders: str,
    masked: bool,
    balance_column: bool,
    two_pages: bool,
    second_account: bool,
    dollars: bool,
) -> SyntheticDocument:
    doc = SyntheticDocument(pages=2 if two_pages else 1)
    doc.text(1, 50, 60, "SYNTHETIC BANK PLC")
    doc.text(1, 50, 75, "Statement of account")
    doc.text(1, 50, 90, holders)  # account holder names: printed, never extracted
    printed_number = f"****{account_number[-4:]}" if masked else account_number
    _row(doc, 1, 120, "Sort code", "**-**-**" if masked else sort_code, 300)
    _row(doc, 1, 135, "Account number", printed_number, 300)
    if second_account:
        _row(doc, 1, 150, "Account number", "87654321", 300)
    first, last = period
    _row(doc, 1, 170, "Statement period", f"{first:%d %B %Y} to {last:%d %B %Y}", 300)
    currency = "$" if dollars else "£"
    _row(doc, 1, 200, "Opening balance", f"{currency}{_plain(opening)}", 300)
    paid_in = sum(r.paid_in or 0 for r in rows)
    paid_out = sum(r.paid_out or 0 for r in rows)
    _row(doc, 1, 215, "Total paid in", f"{currency}{_plain(paid_in)}", 300)
    _row(doc, 1, 230, "Total paid out", f"{currency}{_plain(paid_out)}", 300)
    _row(doc, 1, 245, "Closing balance", f"{currency}{_plain(closing)}", 300)

    def header(page: int, y: float) -> None:
        doc.text(page, _COL_DATE, y, "Date")
        doc.text(page, _COL_DESC, y, "Description")
        doc.right(page, _RIGHT_OUT, y, "Paid out (USD)" if dollars else "Paid out")
        doc.right(page, _RIGHT_IN, y, "Paid in")
        if balance_column:
            doc.right(page, _RIGHT_BAL, y, "Balance")

    page, y = 1, _TABLE_TOP
    header(page, y - 20)
    split_at = len(rows) // 2 if two_pages else None
    running = opening
    drawn: list[StatementRow] = []
    for n, r in enumerate(rows):
        if split_at is not None and n == split_at:
            doc.text(page, _COL_DESC, y, "BALANCE CARRIED FORWARD")
            doc.right(page, _RIGHT_BAL, y, _plain(running))
            drawn.append(
                StatementRow(
                    "carried_forward", None, ["BALANCE CARRIED FORWARD"], None, None, running
                )
            )
            page, y = 2, 80.0
            header(page, y - 20)
            doc.text(page, _COL_DESC, y, "BALANCE BROUGHT FORWARD")
            doc.right(page, _RIGHT_BAL, y, _plain(running))
            drawn.append(
                StatementRow(
                    "brought_forward", None, ["BALANCE BROUGHT FORWARD"], None, None, running
                )
            )
            y += _ROW_STEP
        drawn.append(r)
        if r.posted_on is not None:
            doc.text(page, _COL_DATE, y, f"{r.posted_on:%d %b}")
        for k, line in enumerate(r.description):
            doc.text(page, _COL_DESC, y + k * _ROW_STEP, line)
        if r.paid_out is not None:
            text = _plain(r.paid_out)
            doc.right(page, _RIGHT_OUT, y, text.replace("0", "O", 1) if r.unparsed else text)
        if r.paid_in is not None:
            doc.right(page, _RIGHT_IN, y, _plain(r.paid_in))
        if r.balance is not None and balance_column:
            doc.right(page, _RIGHT_BAL, y, _plain(r.balance))
        running += (r.paid_in or 0) - (r.paid_out or 0)
        y += _ROW_STEP * len(r.description)
        if y > _PAGE_BOTTOM:
            raise ValueError("statement too long for one page; use layout='two_pages'")
    rows[:] = drawn  # the ground truth is what was printed, page-break rows included
    return doc


# --- look-alike documents that must NOT be classified as payslips -------------------------


def _lines(*texts: str) -> SyntheticDocument:
    doc = SyntheticDocument()
    for i, value in enumerate(texts):
        doc.text(1, 50, 60 + 15 * i, value)
    return doc


def p60() -> SyntheticDocument:
    return _lines(
        "P60 End of Year Certificate",
        "Tax year to 5 April 2026",
        "PAYE reference 123/AB456",
        "Pay and Income Tax details",
        "Total for year 38,500.00",
        "National Insurance contributions in this employment",
        "Final tax code 1257L",
    )


def p45() -> SyntheticDocument:
    return _lines(
        "P45 Details of employee leaving work",
        "Employer PAYE reference 123/AB456",
        "Tax code at leaving date 1257L",
        "Total pay to date 21,000.00",
        "Total tax to date 2,150.00",
    )


def sa302() -> SyntheticDocument:
    return _lines(
        "SA302 Tax calculation",
        "Pay from all employments 42,000.00",
        "Total income received 45,000.00",
        "Income Tax due 6,486.00",
        "Class 4 National Insurance contributions 0.00",
    )


def bank_statement() -> SyntheticDocument:
    return _lines(
        "Statement of account",
        "Opening balance 1,200.00",
        "02 Mar ACME LTD NET PAY 2,451.96",
        "05 Mar CARD PAYMENT GROCER 54.20",
        "Closing balance 3,597.76",
    )


def offer_letter() -> SyntheticDocument:
    return _lines(
        "Offer of employment",
        "We are pleased to offer you the role of Analyst.",
        "Your gross pay will be 40,000.00 per year, paid monthly.",
        "Pension deductions will be made at 5% of salary.",
        "Please sign and return one copy.",
    )


def pension_statement() -> SyntheticDocument:
    return _lines(
        "Annual pension statement",
        "Your contributions this year 2,000.00",
        "Employer contributions this year 3,000.00",
        "Projected pension at age 67 12,400.00 per year",
    )


def blank() -> SyntheticDocument:
    return SyntheticDocument()


def filler() -> SyntheticDocument:
    return _lines(
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
        "Ut enim ad minim veniam, quis nostrud exercitation.",
    )


NON_PAYSLIPS = {
    "p60": p60,
    "p45": p45,
    "sa302": sa302,
    "bank-statement": bank_statement,
    "offer-letter": offer_letter,
    "pension-statement": pension_statement,
    "blank": blank,
    "filler": filler,
}
