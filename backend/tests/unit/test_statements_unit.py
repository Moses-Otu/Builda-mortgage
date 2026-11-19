"""Stage 2-B step 2B-1: bank statement classification and extraction, without a database.

Every expectation is the SYNTHETIC generator's ground truth. This proves the pipeline (parser,
provenance, refusals, fingerprints); it says nothing about accuracy on real bank statements,
which needs a real provider and an evaluation corpus (plan, section 3.3)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from meip.documents.extraction import Box, Layout, SyntheticLayoutProvider, classify
from meip.documents.statements import (
    DESCRIPTION_MAX,
    Cell,
    Refusal,
    RowKind,
    StatementExtraction,
    TransactionRow,
    _short_date,
    account_fingerprint,
    extract_statement,
    statement_money,
)
from tests.synthetic_docs import (
    PAYSLIPS,
    SeriesStatement,
    SyntheticDocument,
    bank_statement_series,
    payslip_series,
    salaries_from,
)

KEY = b"unit-test-key"


def layout_of(doc: SyntheticDocument) -> Layout:
    return asyncio.run(SyntheticLayoutProvider().analyse(doc.pdf()))


def statements(**kw: object) -> list[SeriesStatement]:
    return bank_statement_series(salaries_from(payslip_series(months=4)), **kw)  # type: ignore[arg-type]


def as_truth(x: StatementExtraction) -> list[tuple[object, ...]]:
    return [
        (
            r.kind.value,
            r.posted_on,
            r.description_full.split("\n"),
            r.paid_in,
            r.paid_out,
            r.balance,
        )
        for r in x.rows
    ]


def truth(s: SeriesStatement) -> list[tuple[object, ...]]:
    return [
        ("unparsed" if r.unparsed else r.kind, r.posted_on, r.description)
        + ((None, None, None) if r.unparsed else (r.paid_in, r.paid_out, r.balance))
        for r in s.rows
    ]


# --- extraction against ground truth, every layout ---------------------------------------------


@pytest.mark.parametrize("layout", ["standard", "end_of_day", "no_balance", "two_pages"])
def test_every_row_matches_the_ground_truth(layout: str) -> None:
    for s in statements(layout=layout):
        x = extract_statement(layout_of(s.document))
        assert x.is_statement
        assert x.refusal is None
        assert as_truth(x) == truth(s)


def test_summary_fields_with_provenance_and_a_masked_account_number() -> None:
    s = statements()[0]
    x = extract_statement(layout_of(s.document))
    fields = {f.field_code: f for f in x.fields}
    assert fields["account_ending"].value_text == "4821"
    assert fields["account_ending"].raw_text == "****4821"  # the printed number is never kept
    assert fields["statement_period_start"].value_date == date(2026, 5, 1)
    assert fields["statement_period_end"].value_date == date(2026, 5, 31)
    assert fields["opening_balance"].money_minor == s.opening_minor
    assert fields["closing_balance"].money_minor == s.closing_minor
    assert fields["total_paid_in"].money_minor == sum(r.paid_in or 0 for r in s.rows)
    for f in x.fields:  # every value keeps its own label and value box
        assert f.label.box.x1 <= f.value.box.x0


def test_every_cell_traces_to_its_printed_text_and_box() -> None:
    s = statements(layout="two_pages")[1]
    layout = layout_of(s.document)
    printed = {(line.page, line.text, line.box) for line in layout.lines}
    x = extract_statement(layout)
    for row in x.rows:
        assert row.cells, row
        for cell in row.cells:
            assert (cell.page, cell.text, cell.box) in printed
        box = row.row_box
        assert all(
            c.box.x0 >= box.x0 and c.box.x1 <= box.x1 for c in row.cells if c.page == row.page
        )
    multi = next(r for r in x.rows if r.description.startswith("CARD PAYMENT GROCER"))
    assert multi.description == "CARD PAYMENT GROCER\nLONDON GB"  # verbatim, lines kept
    assert {r.page for r in x.rows} == {1, 2}


def test_money_in_and_out_stay_separate() -> None:
    x = extract_statement(layout_of(statements()[0].document))
    salary = next(r for r in x.rows if r.description.startswith("SERIES EMPLOYER"))
    rent = next(r for r in x.rows if r.description.startswith("STANDING ORDER"))
    assert (salary.paid_in, salary.paid_out) == (280_584, None)
    assert (rent.paid_in, rent.paid_out) == (None, 95_000)
    assert salary.cells_json()["paid_in"]["text"] == "2,805.84"


def test_overdrawn_balances_are_negative() -> None:
    x = extract_statement(layout_of(statements(opening_minor=-50_000)[0].document))
    fields = {f.field_code: f for f in x.fields}
    assert fields["opening_balance"].money_minor == -50_000
    assert any(r.balance is not None and r.balance < 0 for r in x.rows)


def test_a_missing_balance_column_is_kept_as_none() -> None:
    x = extract_statement(layout_of(statements(layout="no_balance")[0].document))
    assert not x.has_balance_column
    assert all(r.balance is None for r in x.rows)


# --- unreadable rows and refusals ----------------------------------------------------------------


def test_an_unreadable_row_is_kept_not_dropped() -> None:
    s = statements(positions={"unparsed_row": 0})[0]
    x = extract_statement(layout_of(s.document))
    [bad] = [r for r in x.rows if r.kind is RowKind.UNPARSED]
    assert bad.description == "DIRECT DEBIT ENERGY CO"
    assert (bad.paid_in, bad.paid_out, bad.balance) == (None, None, None)
    assert bad.paid_out_cell is not None
    assert "O" in bad.paid_out_cell.text  # the raw text, exactly as printed
    assert len(x.rows) == len(s.rows)


def test_a_row_missing_from_the_pdf_is_simply_absent() -> None:
    s = statements(positions={"missing_row": 0})[0]
    x = extract_statement(layout_of(s.document))
    assert as_truth(x) == truth(s)
    assert not any(r.description == "CARD PAYMENT CAFE" for r in x.rows)


@pytest.mark.parametrize(
    ("fault", "refusal"),
    [
        ("multiple_accounts", Refusal.MULTIPLE_ACCOUNTS),
        ("foreign_currency", Refusal.FOREIGN_CURRENCY),
    ],
)
def test_the_extractor_refuses_rather_than_guesses(fault: str, refusal: Refusal) -> None:
    s = statements(positions={fault: 0})[0]
    x = extract_statement(layout_of(s.document))
    assert x.refusal is refusal
    assert x.rows == []
    assert x.fields == []
    assert x.account_number is None


def test_no_transactions_table_is_refused() -> None:
    doc = SyntheticDocument()
    doc.text(1, 50, 60, "Statement of account")
    doc.text(1, 50, 80, "Opening balance")
    x = extract_statement(layout_of(doc))
    assert not x.is_statement
    assert x.refusal is Refusal.NO_TABLE


# --- classification both ways ---------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PAYSLIPS))
def test_a_payslip_is_never_a_statement(name: str) -> None:
    x = extract_statement(layout_of(PAYSLIPS[name]()[0]))
    assert not x.is_statement
    assert x.refusal is None


def test_a_series_payslip_is_never_a_statement() -> None:
    assert not extract_statement(layout_of(payslip_series(months=1)[0].document)).is_statement


def test_a_statement_is_never_a_payslip() -> None:
    for s in statements(layout="two_pages"):
        assert not classify(layout_of(s.document).text).is_payslip


# --- account identity -----------------------------------------------------------------------------


def test_the_full_number_is_read_only_for_the_fingerprint() -> None:
    x = extract_statement(layout_of(statements()[0].document))
    assert (x.sort_code, x.account_number) == ("123456", "12344821")
    assert all("12344821" not in f.raw_text and "12-34-56" not in f.raw_text for f in x.fields)


def test_fingerprints_tell_accounts_apart() -> None:
    same_a = account_fingerprint(KEY, "123456", "12344821")
    same_b = account_fingerprint(KEY, "123456", "12344821")
    other_sort = account_fingerprint(KEY, "654321", "12344821")  # same last four digits
    other_key = account_fingerprint(b"another-key", "123456", "12344821")
    assert same_a == same_b
    assert same_a is not None
    assert len(same_a) == 64
    assert len({same_a, other_sort, other_key}) == 3
    assert "12344821" not in same_a


def test_a_masked_number_gives_no_fingerprint_but_keeps_the_last_four() -> None:
    x = extract_statement(layout_of(statements(masked_number=True)[0].document))
    assert x.account_number is None
    assert x.sort_code is None
    assert {f.field_code: f.value_text for f in x.fields}["account_ending"] == "4821"
    assert account_fingerprint(KEY, x.sort_code, x.account_number) is None


def test_two_accounts_sharing_their_last_four_digits() -> None:
    a = extract_statement(layout_of(statements(sort_code="12-34-56")[0].document))
    b = extract_statement(layout_of(statements(sort_code="40-11-22")[0].document))
    last4 = [{f.field_code: f.value_text for f in x.fields}["account_ending"] for x in (a, b)]
    assert last4 == ["4821", "4821"]
    assert account_fingerprint(KEY, a.sort_code, a.account_number) != account_fingerprint(
        KEY, b.sort_code, b.account_number
    )


# --- dates, money and descriptions ---------------------------------------


@pytest.mark.parametrize(
    ("text", "period", "expected"),
    [
        ("05 May", (date(2026, 5, 1), date(2026, 5, 31)), date(2026, 5, 5)),
        ("30 Dec", (date(2026, 12, 15), date(2027, 1, 14)), date(2026, 12, 30)),
        ("03 Jan", (date(2026, 12, 15), date(2027, 1, 14)), date(2027, 1, 3)),
        ("05/05/2026", (None, None), date(2026, 5, 5)),
        ("31 Feb", (date(2026, 2, 1), date(2026, 2, 28)), None),
        ("12 May", (date(2026, 6, 1), date(2026, 6, 30)), None),  # outside the period
    ],
)
def test_short_dates_take_their_year_from_the_period(
    text: str, period: tuple[date | None, date | None], expected: date | None
) -> None:
    assert _short_date(text, period) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,200.00", 120_000),
        ("£1,200.00", 120_000),
        ("-£500.00", -50_000),
        ("£-500.00", -50_000),
        ("(500.00)", -50_000),
        ("500.00 OD", -50_000),
        ("500.00DR", -50_000),
        ("500.00 CR", 50_000),
        ("1,2O0.00", None),
        ("", None),
    ],
)
def test_statement_money(text: str, expected: int | None) -> None:
    assert statement_money(text) == expected


def test_long_descriptions_are_truncated_and_flagged() -> None:
    box = Box(0, 0, 10, 10)
    row = TransactionRow(1, RowKind.TRANSACTION, 1)
    row.description_cells = [Cell("X" * 300, 1, box), Cell("Y" * 300, 1, box)]
    assert len(row.description) == DESCRIPTION_MAX
    assert row.description_truncated
