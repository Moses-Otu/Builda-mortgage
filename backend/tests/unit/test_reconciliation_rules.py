"""The payslip checks (rule set payslip-checks/1) as pure functions: every outcome of every
check, the boundaries, and the finding fingerprints. No database."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest

from meip.reconciliation import rules
from meip.reconciliation.rules import (
    Declared,
    Figure,
    Finding,
    Payslip,
    Reviewed,
    Rule,
    Status,
    run,
)

SOURCE = uuid.UUID("00000000-0000-7000-8000-00000000000a")
APPLICANT = uuid.UUID("00000000-0000-7000-8000-00000000000b")
SOURCES = {SOURCE: APPLICANT}


def _figure(measure: str, amount: int, label: str | None = None, **dates: date | None) -> Figure:
    return Figure(
        observation_id=uuid.uuid4(),
        field_review_id=uuid.uuid4(),
        ordinal=0,
        label=label or measure,
        measure=measure,
        qualifier=None,
        amount=amount,
        frequency="monthly",
        period_start=dates.get("period_start"),
        period_end=dates.get("period_end"),
        as_of=dates.get("as_of"),
    )


def _reviewed(code: str, value: int | date | str) -> Reviewed:
    return Reviewed(uuid.uuid4(), 0, code, code, "accept", value)


def slip(
    period_end: date | None,
    *,
    start: date | None = None,
    pay: date | None = None,
    freq: str = "monthly",
    gross: int | None = 375_000,
    tax: int | None = 54_050,
    deductions: int | None = 94_416,
    net: int | None = 280_584,
    basic: int | None = 375_000,
    ytd_gross: int | None = None,
    ytd_tax: int | None = None,
    labels: tuple[str, ...] = (),
    ytd_label: str = "Gross Pay To Date",
) -> Payslip:
    figures = []
    for measure, amount, label in (
        ("gross_pay", gross, None),
        ("income_tax", tax, None),
        ("net_pay", net, None),
        ("basic_pay", basic, "Basic Pay"),
        ("ytd_gross_pay", ytd_gross, ytd_label),
        ("ytd_income_tax", ytd_tax, None),
    ):
        if amount is not None:
            figures.append(_figure(measure, amount, label, period_end=period_end))
    reviewed = []
    for code, value in (
        ("pay_period_start", start),
        ("pay_period_end", period_end),
        ("pay_date", pay),
        ("total_deductions", deductions),
    ):
        if value is not None:
            reviewed.append(_reviewed(code, value))
    return Payslip(
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        income_source_id=SOURCE,
        applicant_id=APPLICANT,
        frequency=freq,
        figures=tuple(figures),
        reviewed=tuple(reviewed),
        line_labels=("Basic Pay", *labels),
    )


def month(year: int, m: int, **kw: object) -> Payslip:
    """A monthly payslip, 1st to month end, paid on the last weekday; YTD for tax month k."""
    import calendar

    end = date(year, m, calendar.monthrange(year, m)[1])
    pay = end
    while pay.weekday() >= 5:
        pay = date(pay.year, pay.month, pay.day - 1)
    k = (m - 4) % 12 + 1
    defaults: dict[str, object] = {
        "start": date(year, m, 1),
        "pay": pay,
        "ytd_gross": 375_000 * k,
        "ytd_tax": 54_050 * k,
    }
    defaults.update(kw)
    return slip(end, **defaults)  # type: ignore[arg-type]


def findings(payslips: list[Payslip], declared: list[Declared] | None = None) -> list[Finding]:
    return run(payslips, declared or [], SOURCES)


def only(found: list[Finding], rule: Rule, status: Status | None = None) -> list[Finding]:
    return [f for f in found if f.rule is rule and (status is None or f.status is status)]


# --- PAY-ARITH ----------


def test_arithmetic_passes_and_cites_the_three_figures() -> None:
    [f] = only(findings([month(2026, 5)]), Rule.PAY_ARITH)
    assert f.status is Status.PASSED
    assert len(f.cited) == 3


@pytest.mark.parametrize(
    ("net", "status"), [(280_585, Status.PASSED), (280_586, Status.DISCREPANCY)]
)
def test_arithmetic_allows_one_penny(net: int, status: Status) -> None:
    [f] = only(findings([month(2026, 5, net=net)]), Rule.PAY_ARITH)
    assert f.status is status


def test_arithmetic_discrepancy_reports_the_difference() -> None:
    [f] = only(findings([month(2026, 5, net=281_584)]), Rule.PAY_ARITH)
    assert f.status is Status.DISCREPANCY
    assert f.amounts == {
        "expected": 280_584,
        "actual": 281_584,
        "difference": -1_000,
        "allowance": 1,
    }
    assert "Difference £10.00" in f.message


def test_arithmetic_cannot_run_without_total_deductions() -> None:
    [f] = only(findings([month(2026, 5, deductions=None)]), Rule.PAY_ARITH)
    assert f.status is Status.NOT_RUN
    assert "total deductions" in f.message


# --- PERIOD-GAP / PERIOD-DUP ----------


def test_consecutive_months_pass_despite_weekend_pay_dates() -> None:
    # May 2026 ends on a Sunday (paid Fri 29th); August ends on a Monday.
    found = findings([month(2026, m) for m in (5, 6, 7, 8)])
    assert [f.status for f in only(found, Rule.PERIOD_GAP)] == [Status.PASSED]
    assert [f.status for f in only(found, Rule.PERIOD_DUP)] == [Status.PASSED]


def test_irregular_pay_dates_alone_are_never_a_gap() -> None:
    # Paid early for Christmas and late in January: periods still line up.
    dec = month(2026, 12, pay=date(2026, 12, 18))
    jan = month(2027, 1, pay=date(2027, 2, 2))
    assert [f.status for f in only(findings([dec, jan]), Rule.PERIOD_GAP)] == [Status.PASSED]


def test_a_missing_month_is_a_gap_with_its_dates() -> None:
    [gap] = only(findings([month(2026, m) for m in (6, 7, 9)]), Rule.PERIOD_GAP)
    assert gap.status is Status.DISCREPANCY
    assert "1 Aug 2026 to 31 Aug 2026" in gap.title
    assert "About 1 monthly period" in gap.message
    assert len(gap.cited) == 6  # both payslips' period start, end and pay date


def test_a_short_misalignment_cannot_run_rather_than_being_a_gap() -> None:
    june = month(2026, 6, start=date(2026, 6, 1))
    july = month(2026, 7, start=date(2026, 7, 6))
    [f] = only(findings([june, july]), Rule.PERIOD_GAP)
    assert f.status is Status.NOT_RUN
    assert "5 days" in f.message
    assert "1 Jul 2026 to 5 Jul 2026" in f.message


def test_period_ends_only_use_whole_month_steps() -> None:
    feb = month(2026, 2, start=None)
    mar = month(2026, 3, start=None)
    assert only(findings([feb, mar]), Rule.PERIOD_GAP)[0].status is Status.PASSED
    may = month(2026, 5, start=None)
    [gap] = only(findings([mar, may]), Rule.PERIOD_GAP)
    assert gap.status is Status.DISCREPANCY


def test_irregular_period_ends_cannot_run() -> None:
    a = slip(date(2026, 5, 15), pay=date(2026, 5, 15))
    b = slip(date(2026, 6, 30), pay=date(2026, 6, 30))
    [f] = only(findings([a, b]), Rule.PERIOD_GAP)
    assert f.status is Status.NOT_RUN
    assert "irregular" in f.message


def test_pay_dates_alone_cannot_judge_coverage() -> None:
    a = slip(None, pay=date(2026, 5, 29))
    b = slip(None, pay=date(2026, 7, 31))
    [f] = only(findings([a, b]), Rule.PERIOD_GAP)
    assert f.status is Status.NOT_RUN
    assert "don't show their pay periods" in f.message


def test_same_pay_date_without_periods_is_a_duplicate() -> None:
    a = slip(None, pay=date(2026, 5, 29))
    b = slip(None, pay=date(2026, 5, 29))
    [dup] = only(findings([a, b]), Rule.PERIOD_DUP)
    assert dup.status is Status.DISCREPANCY


@pytest.mark.parametrize(
    ("a_start", "b_start"),
    [(date(2026, 6, 1), date(2026, 6, 1)), (date(2026, 6, 1), date(2026, 6, 20))],
)
def test_overlapping_or_repeated_periods_are_duplicates(a_start: date, b_start: date) -> None:
    a = slip(date(2026, 6, 30), start=a_start, pay=date(2026, 6, 30))
    b = slip(date(2026, 7, 19), start=b_start, pay=date(2026, 7, 19))
    if a_start == b_start:
        b = slip(date(2026, 6, 30), start=b_start, pay=date(2026, 6, 30))
    found = findings([a, b])
    assert [f.status for f in only(found, Rule.PERIOD_DUP)] == [Status.DISCREPANCY]
    assert not only(found, Rule.PERIOD_GAP, Status.DISCREPANCY)


def test_weekly_and_four_weekly_periods() -> None:
    w1 = slip(date(2026, 6, 5), start=date(2026, 5, 30), pay=date(2026, 6, 5), freq="weekly")
    w2 = slip(date(2026, 6, 12), start=date(2026, 6, 6), pay=date(2026, 6, 12), freq="weekly")
    assert only(findings([w1, w2]), Rule.PERIOD_GAP)[0].status is Status.PASSED
    f1 = slip(date(2026, 5, 28), pay=date(2026, 5, 28), freq="four_weekly")
    f3 = slip(date(2026, 7, 23), pay=date(2026, 7, 23), freq="four_weekly")  # 56 days later
    assert only(findings([f1, f3]), Rule.PERIOD_GAP)[0].status is Status.DISCREPANCY


def test_mixed_frequencies_cannot_run() -> None:
    a = month(2026, 5)
    b = replace(month(2026, 6), frequency="four_weekly")
    found = findings([a, b])
    assert {f.status for f in only(found, Rule.PERIOD_GAP)} == {Status.NOT_RUN}
    assert {f.status for f in only(found, Rule.PERIOD_DUP)} == {Status.NOT_RUN}


# --- YTD-GROSS / YTD-TAX ----------


def test_year_to_date_passes_on_a_clean_series_and_mentions_the_first() -> None:
    found = findings([month(2026, m) for m in (5, 6, 7)])
    [gross] = only(found, Rule.YTD_GROSS)
    [tax] = only(found, Rule.YTD_TAX)
    assert gross.status is Status.PASSED
    assert tax.status is Status.PASSED
    assert "2 payslips" in gross.title
    assert "May 2026 payslip" in gross.message  # nothing earlier to compare it with


def test_a_year_to_date_break_is_a_discrepancy_citing_both_payslips() -> None:
    # The extra £375 carries forward into July's year to date, as it would on a real payslip.
    slips = [
        month(2026, 5),
        month(2026, 6, ytd_gross=375_000 * 3 + 37_500),
        month(2026, 7, ytd_gross=375_000 * 4 + 37_500),
    ]
    found = only(findings(slips), Rule.YTD_GROSS, Status.DISCREPANCY)
    assert [f.title for f in found] == ["Year to date doesn't add up — June 2026 payslip"]
    [june] = found
    assert june.amounts["difference"] == 37_500
    assert "Difference £375.00 (allowance £1.00)" in june.message
    cited_slips = {c.payslip.document_id for c in june.cited if c.payslip}
    assert cited_slips == {slips[0].document_id, slips[1].document_id}


@pytest.mark.parametrize(("extra", "status"), [(100, Status.PASSED), (101, Status.DISCREPANCY)])
def test_year_to_date_allows_one_pound(extra: int, status: Status) -> None:
    slips = [month(2026, 5), month(2026, 6, ytd_gross=375_000 * 3 + extra)]
    assert only(findings(slips), Rule.YTD_GROSS)[0].status is status


def test_year_to_date_cannot_run_across_a_missing_period() -> None:
    found = findings([month(2026, 6), month(2026, 8)])
    [f] = only(findings([month(2026, 6), month(2026, 8)]), Rule.YTD_GROSS)
    assert f.status is Status.NOT_RUN
    assert "missing" in f.message
    assert only(found, Rule.PERIOD_GAP, Status.DISCREPANCY)  # reported once, by PERIOD-GAP


def test_first_period_of_the_tax_year_must_equal_the_period() -> None:
    march = month(2026, 3)
    april = month(2026, 4)  # tax month 1: YTD = gross
    assert only(findings([march, april]), Rule.YTD_GROSS)[0].status is Status.PASSED
    wrong = month(2026, 4, ytd_gross=500_000)
    [f] = only(findings([march, wrong]), Rule.YTD_GROSS)
    assert f.status is Status.DISCREPANCY
    assert "first pay period of the tax year" in f.message


def test_straddling_tax_years_is_not_compared_as_a_pair() -> None:
    # March (old tax year) then May with no April: May isn't the first period, nothing to compare.
    found = only(findings([month(2026, 3), month(2026, 5)]), Rule.YTD_GROSS)
    assert all(f.status is not Status.DISCREPANCY for f in found)


def test_year_to_date_falling_cannot_run() -> None:
    slips = [month(2026, 6), month(2026, 7, ytd_gross=100_000)]
    [f] = only(findings(slips), Rule.YTD_GROSS)
    assert f.status is Status.NOT_RUN
    assert "fell from" in f.message


def test_taxable_pay_to_date_is_not_compared_with_gross() -> None:
    slips = [
        month(2026, 5, ytd_label="Taxable Pay To Date"),
        month(2026, 6, ytd_label="Taxable Pay To Date"),
    ]
    found = findings(slips)
    assert {f.status for f in only(found, Rule.YTD_GROSS)} == {Status.NOT_RUN}
    assert "taxable pay excludes pension" in only(found, Rule.YTD_GROSS)[0].message
    assert only(found, Rule.YTD_TAX)[0].status is Status.PASSED  # tax still checked


def test_adjustment_lines_turn_a_mismatch_into_could_not_run() -> None:
    slips = [
        month(2026, 5),
        month(2026, 6, ytd_gross=375_000 * 3 + 15_000, labels=("Arrears Adjustment",)),
    ]
    [f] = only(findings(slips), Rule.YTD_GROSS)
    assert f.status is Status.NOT_RUN
    assert "Arrears Adjustment" in f.message
    assert "Check it by eye" in f.message


def test_adjustment_lines_do_not_hide_figures_that_add_up() -> None:
    slips = [month(2026, 5), month(2026, 6, labels=("Tax Refund",))]
    assert only(findings(slips), Rule.YTD_TAX)[0].status is Status.PASSED


def test_year_to_date_needs_known_consecutive_periods() -> None:
    a = slip(None, pay=date(2026, 5, 29), ytd_gross=750_000, ytd_tax=108_100)
    b = slip(None, pay=date(2026, 6, 30), ytd_gross=1_125_000, ytd_tax=162_150)
    [f] = only(findings([a, b]), Rule.YTD_GROSS)
    assert f.status is Status.NOT_RUN
    assert "consecutive" in f.message


# --- DECLARED-BASIC ----------


def declared(annual: int, frequency: str = "annual") -> Declared:
    return Declared(uuid.uuid4(), SOURCE, "basic_pay", annual, frequency)


def test_declared_basic_cannot_run_without_a_declaration() -> None:
    [f] = only(findings([month(2026, 5)]), Rule.DECLARED_BASIC)
    assert f.status is Status.NOT_RUN
    assert "no declared basic salary" in f.message


def test_declared_basic_never_falls_back_to_gross() -> None:
    [f] = only(findings([month(2026, 5, basic=None)], [declared(4_500_000)]), Rule.DECLARED_BASIC)
    assert f.status is Status.NOT_RUN
    assert "no payslip shows a basic pay line" in f.message


@pytest.mark.parametrize(
    ("annual", "status"),
    [
        (3_060_000, Status.PASSED),  # 2,550 x 12 exactly
        (3_000_000, Status.PASSED),  # evidenced exactly 2 % above the declared figure
        (2_999_999, Status.DISCREPANCY),  # just over 2 %
        (3_121_200, Status.PASSED),  # evidenced 1.96 % below the declared figure
        (3_125_000, Status.DISCREPANCY),  # evidenced 2.08 % below
    ],
)
def test_declared_basic_threshold(annual: int, status: Status) -> None:
    [f] = only(findings([month(2026, 5, basic=255_000)], [declared(annual)]), Rule.DECLARED_BASIC)
    assert f.status is status


def test_declared_basic_discrepancy_wording() -> None:
    [f] = only(findings([month(2026, 5)], [declared(5_000_000)]), Rule.DECLARED_BASIC)
    assert "10.0 % lower" in f.title
    assert "not a lender or regulatory standard" in f.message
    assert f.amounts == {"expected": 5_000_000, "actual": 4_500_000, "difference": -500_000}


def test_declared_basic_uses_the_latest_payslip_and_shows_the_range() -> None:
    slips = [month(2026, 5, basic=350_000), month(2026, 6, basic=375_000)]
    [f] = only(findings(slips, [declared(4_500_000)]), Rule.DECLARED_BASIC)
    assert f.status is Status.PASSED
    assert "varied from £3,500.00 to £3,750.00" in f.message


def test_declared_basic_cannot_run_on_a_part_period() -> None:
    [f] = only(
        findings([month(2026, 5, labels=("Unpaid Leave",))], [declared(4_500_000)]),
        Rule.DECLARED_BASIC,
    )
    assert f.status is Status.NOT_RUN
    assert "Unpaid Leave" in f.message


def test_declared_basic_annualises_both_sides() -> None:
    four_weekly = slip(date(2026, 5, 28), pay=date(2026, 5, 28), freq="four_weekly", basic=346_154)
    [f] = only(findings([four_weekly], [declared(375_000, "monthly")]), Rule.DECLARED_BASIC)
    assert f.amounts["expected"] == 4_500_000
    assert f.amounts["actual"] == 346_154 * 13
    assert f.status is Status.PASSED


# --- fingerprints and the rule set ----------


def test_same_inputs_give_the_same_key() -> None:
    slips = [month(2026, 5), month(2026, 6)]
    assert [f.key for f in findings(slips)] == [f.key for f in findings(slips)]
    assert all(len(f.key) == 64 for f in findings(slips))


def test_keys_change_with_any_cited_input() -> None:
    base = month(2026, 5, net=281_584)
    [original] = only(findings([base]), Rule.PAY_ARITH)
    net = base.figures_for("net_pay")[0]
    others = tuple(f for f in base.figures if f is not net)
    new_id = replace(base, figures=(*others, replace(net, observation_id=uuid.uuid4())))
    new_value = replace(base, figures=(*others, replace(net, amount=281_585)))
    new_period = replace(base, figures=(*others, replace(net, period_end=date(2026, 5, 30))))
    new_version = replace(base, document_version_id=uuid.uuid4())
    keys = {
        only(findings([variant]), Rule.PAY_ARITH)[0].key
        for variant in (new_id, new_value, new_period, new_version)
    }
    assert original.key not in keys
    assert len(keys) == 4


def test_keys_change_with_the_rule_set_version(monkeypatch: pytest.MonkeyPatch) -> None:
    slips = [month(2026, 5)]
    before = only(findings(slips), Rule.PAY_ARITH)[0].key
    monkeypatch.setattr(rules, "RULESET_VERSION", "payslip-checks/2")
    assert only(findings(slips), Rule.PAY_ARITH)[0].key != before


def test_keys_change_with_a_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    slips = [month(2026, 5)]
    before = only(findings(slips), Rule.PAY_ARITH)[0].key
    monkeypatch.setitem(rules.PARAMETERS, "ytd_allowance_pence", 200)
    assert only(findings(slips), Rule.PAY_ARITH)[0].key != before


def test_an_income_source_with_nothing_gives_no_findings() -> None:
    assert run([], [], SOURCES) == []


def test_parameters_are_versioned_with_the_rule_set() -> None:
    assert rules.RULESET_VERSION == "payslip-checks/1"
    assert rules.PARAMETERS["declared_basic_threshold_pct"] == "2.0"
