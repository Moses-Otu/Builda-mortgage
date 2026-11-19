"""Stage 2-B: a statement's mathematical validation (totals and running balance), as shown
at confirmation. Pure; checked against the SYNTHETIC generator and hand-made rows."""

from __future__ import annotations

import asyncio
from datetime import date

from meip.documents.extraction import SyntheticLayoutProvider
from meip.documents.statements import (
    Check,
    SimpleRow,
    extract_statement,
    integrity,
)
from tests.synthetic_docs import bank_statement_series, payslip_series, salaries_from


def _checked(**kw: object) -> list[tuple[str, str]]:
    out = []
    for s in bank_statement_series(salaries_from(payslip_series(months=4)), **kw):  # type: ignore[arg-type]
        layout = asyncio.run(SyntheticLayoutProvider().analyse(s.document.pdf()))
        x = extract_statement(layout)
        fields = {f.field_code: f.money_minor for f in x.fields}
        rows = [
            SimpleRow(r.row_number, r.kind.value, r.paid_in, r.paid_out, r.balance, r.posted_on)
            for r in x.rows
        ]
        result = integrity(
            fields.get("opening_balance"),
            fields.get("closing_balance"),
            rows,
            total_in=fields.get("total_paid_in"),
            total_out=fields.get("total_paid_out"),
        )
        out.append((result.totals.value, result.running.value))
    return out


def test_clean_statements_pass_in_every_layout() -> None:
    for layout in ("standard", "end_of_day", "two_pages"):
        assert set(_checked(layout=layout)) == {("passed", "passed")}, layout


def test_no_running_balances_is_not_available_not_failed() -> None:
    assert set(_checked(layout="no_balance")) == {("passed", "not_run")}


def test_a_missing_row_breaks_both_checks() -> None:
    results = _checked(positions={"missing_row": 1})
    assert results[1] == ("discrepancy", "discrepancy")
    assert results[0] == ("passed", "passed")


def test_an_unreadable_row_means_neither_check_can_run() -> None:
    assert _checked(positions={"unparsed_row": 0})[0] == ("not_run", "not_run")


def _row(n: int, kind: str = "transaction", **kw: int | None) -> SimpleRow:
    return SimpleRow(
        n, kind, kw.get("paid_in"), kw.get("paid_out"), kw.get("balance"), date(2026, 5, n)
    )


def test_the_break_names_the_row_and_the_amount() -> None:
    rows = [
        _row(1, paid_out=1_000, balance=9_000),
        _row(2, paid_out=500, balance=8_000),  # should be 8,500: £5.00 unaccounted for
        _row(3, paid_in=200, balance=8_200),
    ]
    result = integrity(10_000, 8_200, rows)
    assert result.running is Check.DISCREPANCY
    assert result.break_row == 2
    assert result.break_difference == -500
    assert "row 2 (02 May)" in result.running_message
    assert "£5.00 unaccounted for" in result.running_message
    assert result.totals is Check.DISCREPANCY  # 10,000 - 1,500 + 200 = 8,700, not 8,200


def test_one_penny_is_allowed() -> None:
    rows = [_row(1, paid_out=1_000, balance=9_001)]
    result = integrity(10_000, 9_001, rows)
    assert (result.totals, result.running) == (Check.PASSED, Check.PASSED)


def test_printed_totals_that_disagree_with_the_rows() -> None:
    rows = [_row(1, paid_in=1_000, balance=11_000)]
    result = integrity(10_000, 11_000, rows, total_in=1_200, total_out=0)
    assert result.totals is Check.DISCREPANCY
    assert "printed total paid in" in result.totals_message


def test_missing_balances_mean_totals_cannot_run() -> None:
    result = integrity(None, 5_000, [_row(1, paid_in=100, balance=5_000)])
    assert result.totals is Check.NOT_RUN
    assert result.running is Check.PASSED  # balances still follow from each other


def test_overdrawn_balances() -> None:
    rows = [_row(1, paid_out=20_000, balance=-10_000), _row(2, paid_in=5_000, balance=-5_000)]
    result = integrity(10_000, -5_000, rows)
    assert (result.totals, result.running) == (Check.PASSED, Check.PASSED)
    assert result.codes() == {"totals": "passed", "running": "passed"}
