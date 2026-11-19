"""Stage 2-B step 2B-3: the bank checks (``bank-checks/1``) as pure functions.

Every outcome of every check: statement totals and running balance (end-of-day balances, no
balance column), gaps between statements, and the four salary-match outcomes with the window
edges, one credit eligible for two payslips, and the joint and cross-applicant relationships.
SYNTHETIC values only."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta

import pytest

from meip.documents import statements as st
from meip.reconciliation import bank_rules as br
from meip.reconciliation import rules
from meip.reconciliation.rules import Status

ALEX, SAM = uuid.uuid4(), uuid.uuid4()
NAMES = {ALEX: "Alex Example", SAM: "Sam Synthetic"}
NET = 280_584
PAY = date(2026, 6, 30)


def account(*holders: uuid.UUID, ending: str = "4821", fingerprinted: bool = True) -> br.Account:
    return br.Account(uuid.uuid4(), ending, None, tuple(holders), fingerprinted)


def value(code: str, v: int | date | str, ordinal: int) -> br.StatementValue:
    return br.StatementValue(uuid.uuid4(), ordinal, code, code, "accept", v)


def txn(
    n: int,
    on: date | None,
    *,
    paid_in: int | None = None,
    paid_out: int | None = None,
    balance: int | None = None,
    kind: str = "transaction",
    description: str = "SOMETHING",
) -> br.Txn:
    return br.Txn(n, kind, on, paid_in, paid_out, balance, description)


def statement(
    acc: br.Account,
    start: date,
    end: date,
    rows: list[br.Txn],
    *,
    opening: int | None = 100_000,
    closing: int | None = None,
    filed_by: uuid.UUID | None = ALEX,
    fingerprinted: bool = True,
) -> br.Statement:
    if closing is None and opening is not None:
        closing = opening + sum((r.paid_in or 0) - (r.paid_out or 0) for r in rows)
    values = [
        value("statement_period_start", start, 1),
        value("statement_period_end", end, 2),
        value("account_ending", acc.ending, 3),
    ]
    if opening is not None:
        values.append(value("opening_balance", opening, 4))
    if closing is not None:
        values.append(value("closing_balance", closing, 5))
    return br.Statement(
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        review_id=uuid.uuid4(),
        account_id=acc.id,
        filing_applicant_id=filed_by,
        fingerprinted=fingerprinted,
        values=tuple(values),
        rows=tuple(rows),
    )


def june(acc: br.Account, *rows: br.Txn, **kw: object) -> br.Statement:
    return statement(acc, date(2026, 6, 1), date(2026, 7, 31), list(rows), **kw)  # type: ignore[arg-type]


def payslip(
    applicant: uuid.UUID = ALEX, *, net: int | None = NET, pay: date | None = PAY
) -> rules.Payslip:
    figures = []
    if net is not None:
        figures.append(
            rules.Figure(
                uuid.uuid4(),
                uuid.uuid4(),
                9,
                "Net pay",
                "net_pay",
                None,
                net,
                "monthly",
                None,
                None,
                pay,
            )
        )
    reviewed = []
    if pay is not None:
        reviewed.append(rules.Reviewed(uuid.uuid4(), 2, "Pay date", "pay_date", "accept", pay))
    return rules.Payslip(
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        income_source_id=uuid.uuid4(),
        applicant_id=applicant,
        frequency="monthly",
        figures=tuple(figures),
        reviewed=tuple(reviewed),
    )


def salary(
    slips: list[rules.Payslip], stmts: list[br.Statement], *accounts: br.Account
) -> list[br.Finding]:
    return br.check_salary_credits(slips, stmts, {a.id: a for a in accounts}, NAMES)


def only(findings: list[br.Finding]) -> br.Finding:
    assert len(findings) == 1, findings
    return findings[0]


# --- the rule set ----------------------------------------------------------------------------


def test_the_rule_set_is_versioned_and_shares_the_balance_allowance() -> None:
    assert br.RULESET_VERSION == "bank-checks/1"
    assert br.RULESET_VERSION != rules.RULESET_VERSION
    assert br.PARAMETERS["balance_allowance_pence"] == st.BALANCE_ALLOWANCE
    assert br.PARAMETERS["near_match_band_pct"] == "10.0"
    assert (br.WINDOW_BEFORE.days, br.WINDOW_AFTER.days, br.EXACT_TOLERANCE) == (3, 5, 0)


def test_a_case_without_confirmed_statements_has_no_bank_findings() -> None:
    assert br.run([payslip()], [], {}, NAMES) == []


# --- BANK-TOTALS and BANK-RUNNING ----------------------------------------------------------------


def _rows(balances: bool = True) -> list[br.Txn]:
    b = (lambda v: v) if balances else (lambda v: None)
    return [
        txn(1, None, kind="brought_forward", balance=b(100_000)),
        txn(2, date(2026, 6, 1), paid_out=95_000, balance=b(5_000)),
        txn(3, date(2026, 6, 5), paid_out=380, balance=b(4_620)),
        txn(4, date(2026, 6, 30), paid_in=NET, balance=b(285_204)),
    ]


def test_a_consistent_statement_passes_both_integrity_checks() -> None:
    acc = account(ALEX)
    totals, running = br.check_integrity(june(acc, *_rows()), acc)
    assert (totals.rule, totals.status) == (br.Rule.BANK_TOTALS, Status.PASSED)
    assert (running.rule, running.status) == (br.Rule.BANK_RUNNING, Status.PASSED)
    assert totals.bank_account_id == acc.id
    assert "account ending 4821" in totals.title


def test_a_missing_row_breaks_the_totals_and_the_running_balance_at_its_row() -> None:
    acc = account(ALEX)
    rows = _rows()
    printed = [rows[0], rows[1], rows[3]]  # the £3.80 row is missing; balances still printed
    s = june(acc, *printed, closing=285_204)
    totals, running = br.check_integrity(s, acc)
    assert totals.status is Status.DISCREPANCY
    assert totals.amounts["difference"] == -380
    assert running.status is Status.DISCREPANCY
    assert "row 4" in running.message
    assert running.amounts["difference"] == -380
    assert any(isinstance(c.item, br.Txn) and c.item.row_number == 4 for c in running.cited)


def test_no_running_balances_is_not_available_never_failed() -> None:
    acc = account(ALEX)
    totals, running = br.check_integrity(june(acc, *_rows(balances=False)), acc)
    assert totals.status is Status.PASSED
    assert running.status is Status.NOT_RUN
    assert "doesn't show running balances" in running.message


def test_an_unreadable_row_means_neither_check_runs() -> None:
    acc = account(ALEX)
    rows = [*_rows(), txn(5, None, kind="unparsed", description="DD ENERGY 89.O0")]
    totals, running = br.check_integrity(june(acc, *rows, closing=285_204), acc)
    assert totals.status is running.status is Status.NOT_RUN
    assert "Row 5" in totals.message


def test_a_rejected_closing_balance_means_the_totals_check_does_not_run() -> None:
    acc = account(ALEX)
    s = june(acc, *_rows())
    s = replace(s, values=tuple(v for v in s.values if v.code != "closing_balance"))
    totals, _ = br.check_integrity(s, acc)
    assert totals.status is Status.NOT_RUN


# --- BANK-GAP ---------------------------------------------------------------------------------


def _month(acc: br.Account, month: int, **kw: object) -> br.Statement:
    start = date(2026, month, 1)
    end = date(2026, month + 1, 1) - timedelta(days=1)
    return statement(acc, start, end, [], **kw)  # type: ignore[arg-type]


def test_consecutive_statements_pass_and_one_statement_gives_nothing() -> None:
    acc = account(ALEX)
    assert br.check_gaps(acc, [_month(acc, 5)]) == []
    found = br.check_gaps(acc, [_month(acc, 6), _month(acc, 5)])  # any order
    assert [f.status for f in found] == [Status.PASSED]
    assert "couldn't be verified" not in found[0].message


def test_a_missing_statement_is_a_gap_with_the_uncovered_days() -> None:
    acc = account(ALEX)
    gap = only(br.check_gaps(acc, [_month(acc, 5), _month(acc, 7)]))
    assert gap.status is Status.DISCREPANCY
    assert "1 Jun 2026 to 30 Jun 2026 (30 days)" in gap.message
    assert "May 2026 statement" in gap.message
    assert "July 2026 statement" in gap.message
    assert len(gap.cited) == 2


def test_continuity_based_on_the_brokers_assignment_says_so() -> None:
    acc = account(ALEX, fingerprinted=False)
    found = only(br.check_gaps(acc, [_month(acc, 5), _month(acc, 6, fingerprinted=False)]))
    assert found.status is Status.PASSED
    assert "couldn't be verified from the documents" in found.message


def test_a_statement_without_a_period_means_continuity_is_not_run() -> None:
    acc = account(ALEX)
    undated = _month(acc, 6)
    undated = replace(undated, values=tuple(v for v in undated.values if "period" not in v.code))
    assert only(br.check_gaps(acc, [_month(acc, 5), undated])).status is Status.NOT_RUN


# --- SALARY-CREDIT ------------------------------------------------------------------------------


def test_an_exact_credit_into_a_joint_account_is_received_with_the_relationship() -> None:
    joint = account(ALEX, SAM)
    s = june(
        joint, txn(7, PAY, paid_in=NET, description="SERIES EMPLOYER LTD\nSALARY"), filed_by=SAM
    )
    f = only(salary([payslip()], [s], joint))
    assert (f.status, f.match_outcome) == (Status.PASSED, br.Outcome.EXACT)
    assert f.message == (
        "Received: £2,805.84 on 30 Jun 2026, 'SERIES EMPLOYER LTD / SALARY', in account "
        "ending 4821 (joint: Alex Example and Sam Synthetic), statement filed under Sam "
        "Synthetic."
    )
    rel = f.relationship
    assert rel is not None
    assert (rel.joint, rel.applicant_holds_account, rel.filing_applicant_id) == (True, True, SAM)
    assert (rel.document_id, rel.row_number) == (s.document_id, 7)
    assert f.amounts == {"expected": NET, "actual": NET}


def test_an_exact_credit_into_an_account_the_applicant_does_not_hold_is_a_discrepancy() -> None:
    sams = account(SAM, ending="7710")
    f = only(salary([payslip(ALEX)], [june(sams, txn(3, PAY, paid_in=NET), filed_by=SAM)], sams))
    assert (f.status, f.match_outcome) == (Status.DISCREPANCY, br.Outcome.EXACT)
    assert "account ending 7710, held only by Sam Synthetic" in f.message
    assert "Alex Example (the payslip's applicant) isn't a recorded holder" in f.message
    assert f.relationship is not None
    assert not f.relationship.applicant_holds_account


@pytest.mark.parametrize(("offset", "found"), [(-3, True), (5, True), (-4, False), (6, False)])
def test_the_window_runs_from_three_days_before_to_five_after(offset: int, found: bool) -> None:
    acc = account(ALEX)
    s = june(acc, txn(2, PAY + timedelta(days=offset), paid_in=NET))
    f = only(salary([payslip()], [s], acc))
    assert f.match_outcome is (br.Outcome.EXACT if found else br.Outcome.NONE)


def test_two_credits_of_the_net_pay_are_multiple_possible_matches() -> None:
    acc = account(ALEX)
    s = june(
        acc,
        txn(2, PAY - timedelta(days=2), paid_in=NET, description="TRANSFER FROM J SAMPLE"),
        txn(3, PAY, paid_in=NET, description="SERIES EMPLOYER LTD"),
    )
    f = only(salary([payslip()], [s], acc))
    assert (f.status, f.match_outcome) == (Status.DISCREPANCY, br.Outcome.MULTIPLE)
    assert "2 credits of £2,805.84 between 27 Jun 2026 and 5 Jul 2026" in f.message
    assert "TRANSFER FROM J SAMPLE" in f.message
    assert "SERIES EMPLOYER LTD" in f.message
    assert sum(isinstance(c.item, br.Txn) for c in f.cited) == 2


def test_one_credit_cannot_confirm_two_payslips() -> None:
    acc = account(ALEX)
    s = june(acc, txn(2, PAY, paid_in=NET))
    first, second = payslip(pay=PAY), payslip(pay=PAY + timedelta(days=2))
    found = salary([first, second], [s], acc)
    assert [f.match_outcome for f in found] == [br.Outcome.MULTIPLE, br.Outcome.MULTIPLE]
    assert all(f.status is Status.DISCREPANCY for f in found)
    assert "also the only possible match" in found[0].message


def test_a_credit_shared_with_a_payslip_that_has_another_candidate_still_matches() -> None:
    acc = account(ALEX)
    s = june(acc, txn(2, PAY, paid_in=NET), txn(3, PAY + timedelta(days=6), paid_in=NET))
    first, second = payslip(pay=PAY), payslip(pay=PAY + timedelta(days=2))
    found = salary([first, second], [s], acc)
    assert [f.match_outcome for f in found] == [br.Outcome.EXACT, br.Outcome.MULTIPLE]


def test_a_near_credit_is_never_treated_as_received() -> None:
    acc = account(ALEX)
    s = june(acc, txn(2, PAY, paid_in=NET - 150), txn(3, PAY, paid_in=NET - 20_000))
    f = only(salary([payslip()], [s], acc))
    assert (f.status, f.match_outcome) == (Status.DISCREPANCY, br.Outcome.NEAR)
    assert f.message.startswith("No credit of exactly £2,805.84 between 27 Jun 2026")
    assert "£2,804.34 on 30 Jun 2026, 'SOMETHING', £1.50 less" in f.message
    assert "not treated as received" in f.message
    assert f.amounts == {"expected": NET, "actual": NET - 150, "difference": -150}
    assert f.relationship is None


def test_beyond_the_near_band_is_no_plausible_match_listing_the_accounts_searched() -> None:
    a, b = account(ALEX), account(ALEX, SAM, ending="7710")
    far = NET * 89 // 100  # 11 % short: outside the 10 % band
    f = only(salary([payslip()], [june(a, txn(2, PAY, paid_in=far)), june(b)], a, b))
    assert (f.status, f.match_outcome) == (Status.DISCREPANCY, br.Outcome.NONE)
    assert f.message == (
        "No credit within 10.0% of £2,805.84 between 27 Jun 2026 and 5 Jul 2026 in accounts "
        "ending 4821, 7710."
    )


def test_matching_never_uses_the_description() -> None:
    acc = account(ALEX)
    plain = june(acc, txn(2, PAY, paid_in=NET, description="SERIES EMPLOYER LTD SALARY"))
    odd = june(acc, txn(2, PAY, paid_in=NET, description="REFUND FROM SHOP"))
    outcomes = [only(salary([payslip()], [s], acc)).match_outcome for s in (plain, odd)]
    assert outcomes == [br.Outcome.EXACT, br.Outcome.EXACT]


def test_an_uncovered_window_is_not_run() -> None:
    acc = account(ALEX)
    may = statement(acc, date(2026, 5, 1), date(2026, 6, 28), [])  # ends inside the window
    f = only(salary([payslip()], [may], acc))
    assert f.status is Status.NOT_RUN
    assert f.message.startswith("No confirmed statement covers 27 Jun 2026 to 5 Jul 2026")


def test_two_statements_of_one_account_together_cover_the_window() -> None:
    acc = account(ALEX)
    june_only = statement(acc, date(2026, 6, 1), date(2026, 6, 30), [txn(2, PAY, paid_in=NET)])
    july = statement(acc, date(2026, 7, 1), date(2026, 7, 31), [])
    assert only(salary([payslip()], [june_only, july], acc)).match_outcome is br.Outcome.EXACT


def test_an_unreadable_row_in_the_window_means_salary_is_not_run() -> None:
    acc = account(ALEX)
    s = june(acc, txn(2, PAY, paid_in=NET), txn(3, None, kind="unparsed"))
    f = only(salary([payslip()], [s], acc))
    assert f.status is Status.NOT_RUN
    assert "Row 3 of the July 2026 statement" in f.message


def test_a_payslip_without_net_pay_or_pay_date_is_not_run() -> None:
    acc = account(ALEX)
    s = june(acc, txn(2, PAY, paid_in=NET))
    for slip in (payslip(net=None), payslip(pay=None)):
        assert only(salary([slip], [s], acc)).status is Status.NOT_RUN


# --- fingerprints ----------------------------------------------------------------------------


def test_finding_keys_are_stable_and_change_with_any_input() -> None:
    sams = account(SAM)
    slip = payslip(ALEX)
    s = june(sams, txn(3, PAY, paid_in=NET))
    key = only(salary([slip], [s], sams)).key
    assert only(salary([slip], [s], sams)).key == key
    # Adding Alex as a holder changes the outcome and the key.
    both = replace(sams, holder_ids=(SAM, ALEX))
    after = only(salary([slip], [s], both))
    assert after.status is Status.PASSED
    assert after.key != key
    # Another statement in the window changes the key, even with the same outcome.
    extra = june(account(SAM), filed_by=SAM)
    assert only(salary([slip], [s, extra], sams, account(SAM))).key != key


def test_run_gives_every_check() -> None:
    acc = account(ALEX)
    may, jun = _month(acc, 5), _month(acc, 6)
    jun = replace(jun, rows=(txn(2, PAY, paid_in=NET),))
    jun = replace(
        jun,
        values=(
            *tuple(v for v in jun.values if v.code != "closing_balance"),
            value("closing_balance", 100000 + NET, 5),
        ),
    )
    found = br.run([payslip()], [jun, may], {acc.id: acc}, NAMES)
    assert [f.rule for f in found] == [
        br.Rule.BANK_TOTALS,
        br.Rule.BANK_RUNNING,
        br.Rule.BANK_TOTALS,
        br.Rule.BANK_RUNNING,
        br.Rule.BANK_GAP,
        br.Rule.SALARY_CREDIT,
    ]
    # The window runs to 5 Jul, past the June statement: the salary can't be checked yet.
    assert found[-1].status is Status.NOT_RUN
