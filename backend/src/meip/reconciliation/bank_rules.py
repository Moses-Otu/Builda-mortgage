"""The bank statement checks (rule set ``bank-checks/1``): pure functions over plain data.

Separate from ``payslip-checks/1`` and versioned on its own. The inputs are **confirmed**
statements (summary values as accepted or corrected, the account the broker chose and its
holders, the extracted rows) and the reviewed payslips. Each check returns findings with
one of three statuses: ``passed``, ``discrepancy`` or ``not_run`` (always with a reason). The
checks decide nothing about the case and create no financial figures.

- ``BANK-TOTALS`` and ``BANK-RUNNING``: the statement's numbers are internally consistent
  (:func:`meip.documents.statements.integrity`, the same function the confirmation screen
  uses). No running balances printed means the row-by-row check is **not available**, never
  failed.
- ``BANK-GAP``: consecutive statements of one confirmed account leave no uncovered days.
- ``SALARY-CREDIT``: whether each reviewed payslip's net pay can be seen arriving, with four
  distinct outcomes (exact, multiple, near, none). **A near match is never treated as
  received**, and matching never uses the description.

Changing any value in :data:`PARAMETERS` or any rule's behaviour means a new
:data:`RULESET_VERSION`. The match window and the near-match band are initial product
assumptions, not regulatory or lender standards.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from meip.documents import statements as st
from meip.reconciliation import rules
from meip.reconciliation.rules import Status, money

RULESET_VERSION = "bank-checks/1"

PARAMETERS: dict[str, Any] = {
    "balance_allowance_pence": 1,
    # Initial product assumptions, not regulatory or lender standards.
    "match_window_days_before": 3,
    "match_window_days_after": 5,
    "exact_match_tolerance_pence": 0,
    "near_match_band_pct": "10.0",
    "near_matches_shown": 3,
}

WINDOW_BEFORE = timedelta(days=int(PARAMETERS["match_window_days_before"]))
WINDOW_AFTER = timedelta(days=int(PARAMETERS["match_window_days_after"]))
EXACT_TOLERANCE = int(PARAMETERS["exact_match_tolerance_pence"])
NEAR_PCT = Decimal(PARAMETERS["near_match_band_pct"])
NEAR_SHOWN = int(PARAMETERS["near_matches_shown"])
ONE_DAY = timedelta(days=1)


class Rule(StrEnum):
    BANK_TOTALS = "BANK-TOTALS"
    BANK_RUNNING = "BANK-RUNNING"
    BANK_GAP = "BANK-GAP"
    SALARY_CREDIT = "SALARY-CREDIT"


class Outcome(StrEnum):
    EXACT = "exact"
    MULTIPLE = "multiple"
    NEAR = "near"
    NONE = "none"


# --- inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Account:
    id: uuid.UUID
    ending: str
    label: str | None
    holder_ids: tuple[uuid.UUID, ...]
    fingerprinted: bool

    @property
    def joint(self) -> bool:
        return len(self.holder_ids) > 1

    @property
    def name(self) -> str:
        return f"account ending {self.ending}" + (f" ({self.label})" if self.label else "")


@dataclass(frozen=True)
class Txn:
    """One extracted row, as printed. The description is verbatim and never used to match."""

    row_number: int
    kind: str
    posted_on: date | None
    paid_in: int | None
    paid_out: int | None
    balance: int | None
    description: str

    @property
    def one_line(self) -> str:
        return " / ".join(part for part in self.description.splitlines() if part.strip())


@dataclass(frozen=True)
class StatementValue:
    """A confirmed summary value (accepted or corrected). Rejected values are never loaded."""

    field_review_id: uuid.UUID
    ordinal: int
    label: str
    code: str
    decision: str
    value: int | date | str


@dataclass(frozen=True)
class Statement:
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    review_id: uuid.UUID
    account_id: uuid.UUID
    filing_applicant_id: uuid.UUID | None
    fingerprinted: bool  # the document itself carried a full account number
    values: tuple[StatementValue, ...] = ()
    rows: tuple[Txn, ...] = ()

    def value(self, code: str) -> StatementValue | None:
        return next((v for v in self.values if v.code == code), None)

    def money_of(self, code: str) -> int | None:
        v = self.value(code)
        return v.value if v is not None and isinstance(v.value, int) else None

    def date_of(self, code: str) -> date | None:
        v = self.value(code)
        return v.value if v is not None and isinstance(v.value, date) else None

    @property
    def period_start(self) -> date | None:
        return self.date_of("statement_period_start")

    @property
    def period_end(self) -> date | None:
        return self.date_of("statement_period_end")

    @property
    def name(self) -> str:
        """'June 2026 statement', from the period end."""
        d = self.period_end or self.period_start
        return f"{d:%B %Y} statement" if d else "statement"

    @property
    def sort_key(self) -> tuple[date, date, str]:
        return (
            self.period_start or date.max,
            self.period_end or date.max,
            str(self.document_id),
        )


Cite = rules.Figure | rules.Reviewed | StatementValue | Txn


@dataclass(frozen=True)
class Cited:
    """One cited input, with the payslip or statement it came from."""

    item: Cite
    payslip: rules.Payslip | None = None
    statement: Statement | None = None


@dataclass(frozen=True)
class Relationship:
    """Who the matched money relates to: the payslip's applicant, the account it arrived in
    (sole or joint, its holders), who the statement was filed under, and the row."""

    payslip_applicant_id: uuid.UUID
    account_id: uuid.UUID
    account_ending: str
    account_label: str | None
    joint: bool
    holder_ids: tuple[uuid.UUID, ...]
    applicant_holds_account: bool
    filing_applicant_id: uuid.UUID | None
    document_id: uuid.UUID
    row_number: int


@dataclass(frozen=True)
class Finding:
    rule: Rule
    status: Status
    title: str
    message: str
    cited: tuple[Cited, ...] = ()
    amounts: dict[str, int] = field(default_factory=dict)
    key: str = ""
    income_source_id: uuid.UUID | None = None
    applicant_id: uuid.UUID | None = None
    bank_account_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    match_outcome: Outcome | None = None
    relationship: Relationship | None = None


# --- fingerprints -----------------------------------------------------------------------------


def _canonical(cited: Cited) -> dict[str, Any]:
    item = cited.item
    if isinstance(item, rules.Figure | rules.Reviewed):
        return rules._canonical(rules.Cited(item, cited.payslip))
    out: dict[str, Any]
    if isinstance(item, StatementValue):
        out = {
            "field_review_id": item.field_review_id,
            "code": item.code,
            "decision": item.decision,
            "value": item.value,
        }
    else:
        out = {
            "row_number": item.row_number,
            "kind": item.kind,
            "posted_on": item.posted_on,
            "paid_in": item.paid_in,
            "paid_out": item.paid_out,
            "balance": item.balance,
        }
    if cited.statement is not None:
        s = cited.statement
        out["document_version_id"] = s.document_version_id
        out["review_id"] = s.review_id
        out["account_id"] = s.account_id
    return out


def fingerprint(
    rule: Rule, subject: uuid.UUID, cited: Sequence[Cited], context: dict[str, Any]
) -> str:
    """SHA-256 over the rule-set version, rule, parameters, subject, context (for example
    the account's holders, or the statements searched) and every cited input. Any change to
    any of them gives a different key, so an acknowledgement never carries over."""
    document = {
        "ruleset": RULESET_VERSION,
        "rule": rule.value,
        "parameters": PARAMETERS,
        "subject": subject,
        "context": context,
        "cited": sorted(
            (_canonical(c) for c in cited), key=lambda c: json.dumps(c, sort_keys=True, default=str)
        ),
    }
    encoded = json.dumps(document, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _finding(
    rule: Rule,
    status: Status,
    title: str,
    message: str,
    *,
    subject: uuid.UUID,
    cited: Sequence[Cited],
    context: dict[str, Any] | None = None,
    **extra: Any,
) -> Finding:
    return Finding(
        rule=rule,
        status=status,
        title=title,
        message=message,
        cited=tuple(cited),
        key=fingerprint(rule, subject, cited, context or {}),
        **extra,
    )


# --- helpers ----------------------------------------------------------------------------------


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _values(s: Statement, *codes: str) -> list[Cited]:
    return [Cited(v, statement=s) for c in codes if (v := s.value(c)) is not None]


def _names(ids: Iterable[uuid.UUID], names: dict[uuid.UUID, str]) -> str:
    found = [names.get(i, "an applicant") for i in ids]
    if len(found) <= 1:
        return found[0] if found else "no one"
    return f"{', '.join(found[:-1])} and {found[-1]}"


def _holders(account: Account, names: dict[uuid.UUID, str]) -> str:
    who = _names(account.holder_ids, names)
    return f"joint: {who}" if account.joint else f"held only by {who}"


# --- BANK-TOTALS and BANK-RUNNING -------------------------------------------------------------


def check_integrity(s: Statement, account: Account) -> list[Finding]:
    rows = [
        st.SimpleRow(r.row_number, r.kind, r.paid_in, r.paid_out, r.balance, r.posted_on)
        for r in s.rows
    ]
    result = st.integrity(
        s.money_of("opening_balance"),
        s.money_of("closing_balance"),
        rows,
        total_in=s.money_of("total_paid_in"),
        total_out=s.money_of("total_paid_out"),
    )
    where = f"{s.name}, {account.name}"
    common: dict[str, Any] = {"bank_account_id": account.id, "document_id": s.document_id}
    totals_cited = _values(
        s, "opening_balance", "closing_balance", "total_paid_in", "total_paid_out"
    )
    totals_amounts = {"allowance": st.BALANCE_ALLOWANCE}
    if result.totals_difference is not None:
        totals_amounts["difference"] = result.totals_difference
    totals = _finding(
        Rule.BANK_TOTALS,
        Status(result.totals.value),
        f"Statement totals: {where}",
        _sentence(result.totals_message) + ".",
        subject=s.document_id,
        cited=totals_cited,
        amounts=totals_amounts,
        **common,
    )

    running_cited = _values(s, "opening_balance")
    running_amounts: dict[str, int] = {"allowance": st.BALANCE_ALLOWANCE}
    broken = next((r for r in s.rows if r.row_number == result.break_row), None)
    if broken is not None:
        running_cited.append(Cited(broken, statement=s))
        if result.break_difference is not None:
            running_amounts["difference"] = result.break_difference
    running = _finding(
        Rule.BANK_RUNNING,
        Status(result.running.value),
        f"Running balance: {where}",
        _sentence(result.running_message) + ".",
        subject=s.document_id,
        cited=running_cited,
        amounts=running_amounts,
        **common,
    )
    return [totals, running]


# --- BANK-GAP ---------------------------------------------------------------------------------


def check_gaps(account: Account, statements: list[Statement]) -> list[Finding]:
    """Consecutive statements of one account leave no uncovered days. One statement alone has
    nothing to compare, so it gives no finding."""
    if len(statements) < 2:
        return []
    common: dict[str, Any] = {"bank_account_id": account.id}
    undated = [s for s in statements if s.period_start is None or s.period_end is None]
    if undated:
        s = undated[0]
        return [
            _finding(
                Rule.BANK_GAP,
                Status.NOT_RUN,
                f"Statement continuity: {account.name}",
                f"The {s.name} has no confirmed statement period, so the continuity of "
                f"{account.name} can't be checked.",
                subject=account.id,
                cited=_values(s, "statement_period_start", "statement_period_end"),
                document_id=s.document_id,
                **common,
            )
        ]
    ordered = sorted(statements, key=lambda s: s.sort_key)
    findings: list[Finding] = []
    for a, b in itertools.pairwise(ordered):
        if a.period_end is None or b.period_start is None:
            continue  # excluded above: every statement here has a period
        cited = [*_values(a, "statement_period_end"), *_values(b, "statement_period_start")]
        unverified = not (a.fingerprinted and b.fingerprinted)
        note = (
            " These statements are linked to the account by your confirmation: the account "
            "couldn't be verified from the documents."
            if unverified
            else ""
        )
        first_uncovered, last_uncovered = a.period_end + ONE_DAY, b.period_start - ONE_DAY
        if first_uncovered <= last_uncovered:
            days = (last_uncovered - first_uncovered).days + 1
            findings.append(
                _finding(
                    Rule.BANK_GAP,
                    Status.DISCREPANCY,
                    f"Statement gap: {account.name}",
                    f"No statement covers {_day(first_uncovered)} to {_day(last_uncovered)} "
                    f"({days} {'day' if days == 1 else 'days'}), between the {a.name} and the "
                    f"{b.name}.{note}",
                    subject=account.id,
                    cited=cited,
                    document_id=b.document_id,
                    **common,
                )
            )
        else:
            findings.append(
                _finding(
                    Rule.BANK_GAP,
                    Status.PASSED,
                    f"Statement continuity: {account.name}",
                    f"The {b.name} follows on from the {a.name} with no uncovered days.{note}",
                    subject=account.id,
                    cited=cited,
                    document_id=b.document_id,
                    **common,
                )
            )
    return findings


# --- SALARY-CREDIT ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Credit:
    statement: Statement
    row: Txn

    @property
    def amount(self) -> int:
        return self.row.paid_in or 0

    @property
    def ident(self) -> tuple[uuid.UUID, int]:
        return (self.statement.document_version_id, self.row.row_number)

    def describe(self) -> str:
        when = f" on {_day(self.row.posted_on)}" if self.row.posted_on else ""
        return f"{money(self.amount)}{when}, '{self.row.one_line}'"


def covers(periods: Iterable[tuple[date, date]], lo: date, hi: date) -> bool:
    """Whether the periods together cover every day from ``lo`` to ``hi``."""
    reach = lo  # the first day not yet covered
    for start, end in sorted(periods):
        if start > reach:
            return False
        reach = max(reach, end + ONE_DAY)
        if reach > hi:
            return True
    return reach > hi


@dataclass
class _Prepared:
    payslip: rules.Payslip
    net: int
    lo: date
    hi: date
    searched: list[Statement]
    credits: list[_Credit]
    exact: list[_Credit]


def _payslip_cites(p: rules.Payslip) -> list[Cited]:
    """The payslip's net pay and pay date."""
    pay_date = p.field("pay_date")
    return [
        *(Cited(f, payslip=p) for f in p.figures_for("net_pay")),
        *([Cited(pay_date, payslip=p)] if pay_date is not None else []),
    ]


def _near(net: int, amount: int) -> bool:
    return Decimal(abs(amount - net)) * 100 <= Decimal(net) * NEAR_PCT


def check_salary_credits(
    payslips: Sequence[rules.Payslip],
    statements: Sequence[Statement],
    accounts: dict[uuid.UUID, Account],
    names: dict[uuid.UUID, str],
) -> list[Finding]:
    """One finding per reviewed payslip. Candidates are assigned across all payslips together,
    so one credit can't confirm two payslips."""
    findings: list[Finding] = []
    prepared: list[_Prepared] = []
    for p in payslips:
        source: dict[str, Any] = {
            "income_source_id": p.income_source_id,
            "applicant_id": p.applicant_id,
        }
        net, pay = p.amount("net_pay"), p.pay_date
        base_cited = _payslip_cites(p)
        title = f"{p.month} salary"
        if net is None or pay is None or net <= 0:
            findings.append(
                _finding(
                    Rule.SALARY_CREDIT,
                    Status.NOT_RUN,
                    title,
                    f"The {p.name} has no reviewed net pay or pay date, so its arrival can't "
                    "be looked for.",
                    subject=p.document_version_id,
                    cited=base_cited,
                    **source,
                )
            )
            continue
        lo, hi = pay - WINDOW_BEFORE, pay + WINDOW_AFTER
        window = f"between {_day(lo)} and {_day(hi)}"
        searched = sorted(
            (
                s
                for s in statements
                if s.period_start is not None
                and s.period_end is not None
                and s.period_start <= hi
                and s.period_end >= lo
            ),
            key=lambda s: s.sort_key,
        )
        context = {"searched": [s.review_id for s in searched]}
        unreadable = [
            (s, r)
            for s in searched
            for r in s.rows
            if r.kind == "unparsed" and (r.posted_on is None or lo <= r.posted_on <= hi)
        ]
        if unreadable:
            s, r = unreadable[0]
            findings.append(
                _finding(
                    Rule.SALARY_CREDIT,
                    Status.NOT_RUN,
                    title,
                    f"Row {r.row_number} of the {s.name} ({accounts[s.account_id].name}) "
                    f"couldn't be read, so the credits {window} can't be relied on.",
                    subject=p.document_version_id,
                    cited=[*base_cited, Cited(r, statement=s)],
                    context=context,
                    **source,
                )
            )
            continue
        by_account: dict[uuid.UUID, list[tuple[date, date]]] = {}
        for s in searched:
            if s.period_start is not None and s.period_end is not None:
                by_account.setdefault(s.account_id, []).append((s.period_start, s.period_end))
        if not any(covers(periods, lo, hi) for periods in by_account.values()):
            findings.append(
                _finding(
                    Rule.SALARY_CREDIT,
                    Status.NOT_RUN,
                    title,
                    f"No confirmed statement covers {_day(lo)} to {_day(hi)} (the "
                    f"{p.name}'s pay date {_day(pay)}, {WINDOW_BEFORE.days} days before to "
                    f"{WINDOW_AFTER.days} after), so its arrival can't be checked.",
                    subject=p.document_version_id,
                    cited=base_cited,
                    context=context,
                    **source,
                )
            )
            continue
        found = [
            _Credit(s, r)
            for s in searched
            for r in s.rows
            if r.kind == "transaction"
            and r.paid_in
            and r.posted_on is not None
            and lo <= r.posted_on <= hi
        ]
        exact = [c for c in found if abs(c.amount - net) <= EXACT_TOLERANCE]
        prepared.append(_Prepared(p, net, lo, hi, searched, found, exact))

    # A credit that is the only exact candidate for more than one payslip confirms none.
    sole: dict[tuple[uuid.UUID, int], list[rules.Payslip]] = {}
    for prep in prepared:
        if len(prep.exact) == 1:
            sole.setdefault(prep.exact[0].ident, []).append(prep.payslip)

    for prep in prepared:
        findings.append(_salary_finding(prep, sole, accounts, names))
    return findings


def _salary_finding(
    prep: _Prepared,
    sole: dict[tuple[uuid.UUID, int], list[rules.Payslip]],
    accounts: dict[uuid.UUID, Account],
    names: dict[uuid.UUID, str],
) -> Finding:
    p, net = prep.payslip, prep.net
    window = f"between {_day(prep.lo)} and {_day(prep.hi)}"
    base_cited = _payslip_cites(p)
    context: dict[str, Any] = {"searched": [s.review_id for s in prep.searched]}
    common: dict[str, Any] = {
        "subject": p.document_version_id,
        "income_source_id": p.income_source_id,
        "applicant_id": p.applicant_id,
    }

    def cite(chosen: Iterable[_Credit]) -> list[Cited]:
        return [*base_cited, *(Cited(c.row, statement=c.statement) for c in chosen)]

    if len(prep.exact) == 1 and len(sole.get(prep.exact[0].ident, [])) == 1:
        c = prep.exact[0]
        account = accounts[c.statement.account_id]
        holds = p.applicant_id in account.holder_ids
        filed = (
            f", statement filed under {names.get(c.statement.filing_applicant_id, 'an applicant')}"
            if c.statement.filing_applicant_id
            else ""
        )
        context["holders"] = sorted(str(h) for h in account.holder_ids)
        relationship = Relationship(
            payslip_applicant_id=p.applicant_id,
            account_id=account.id,
            account_ending=account.ending,
            account_label=account.label,
            joint=account.joint,
            holder_ids=account.holder_ids,
            applicant_holds_account=holds,
            filing_applicant_id=c.statement.filing_applicant_id,
            document_id=c.statement.document_id,
            row_number=c.row.row_number,
        )
        received = f"Received: {c.describe()}, in {account.name}"
        if holds:
            label = f"{account.label}; " if account.label else ""
            return _finding(
                Rule.SALARY_CREDIT,
                Status.PASSED,
                f"{p.month} salary received",
                f"Received: {c.describe()}, in account ending {account.ending} "
                f"({label}{_holders(account, names)}){filed}.",
                cited=cite([c]),
                context=context,
                amounts={"expected": net, "actual": c.amount},
                bank_account_id=account.id,
                document_id=c.statement.document_id,
                match_outcome=Outcome.EXACT,
                relationship=relationship,
                **common,
            )
        applicant = names.get(p.applicant_id, "The payslip's applicant")
        return _finding(
            Rule.SALARY_CREDIT,
            Status.DISCREPANCY,
            f"{p.month} salary received in an account {applicant} doesn't hold",
            f"{received}, {_holders(account, names)}{filed}. {applicant} (the payslip's "
            "applicant) isn't a recorded holder of this account.",
            cited=cite([c]),
            context=context,
            amounts={"expected": net, "actual": c.amount},
            bank_account_id=account.id,
            document_id=c.statement.document_id,
            match_outcome=Outcome.EXACT,
            relationship=relationship,
            **common,
        )

    if prep.exact:
        if len(prep.exact) == 1:
            c = prep.exact[0]
            others = [o for o in sole[c.ident] if o is not p]
            other = others[0].name if others else "another payslip"
            message = (
                f"The only credit of {money(net)} {window} ({c.describe()}) is also the only "
                f"possible match for the {other}: it can't be told which payslip it is."
            )
        else:
            listed = "; ".join(c.describe() for c in prep.exact)
            message = (
                f"{len(prep.exact)} credits of {money(net)} {window} ({listed}): it can't be "
                "told which, if either, is this salary."
            )
        return _finding(
            Rule.SALARY_CREDIT,
            Status.DISCREPANCY,
            f"{p.month} salary: more than one possible credit",
            message,
            cited=cite(prep.exact),
            context=context,
            amounts={"expected": net},
            match_outcome=Outcome.MULTIPLE,
            **common,
        )

    near = sorted(
        (c for c in prep.credits if _near(net, c.amount)),
        key=lambda c: (abs(c.amount - net), c.row.posted_on, c.row.row_number),
    )[:NEAR_SHOWN]
    if near:
        closest = near[0]
        diff = closest.amount - net

        def gap(c: _Credit) -> str:
            d = c.amount - net
            return f"{money(abs(d))} {'less' if d < 0 else 'more'}"

        listed = "; ".join(f"{c.describe()}, {gap(c)}" for c in near)
        return _finding(
            Rule.SALARY_CREDIT,
            Status.DISCREPANCY,
            f"{p.month} salary: near match, not confirmed",
            f"No credit of exactly {money(net)} {window}. Closest: {listed}. A near match "
            f"(within {NEAR_PCT}%) is not treated as received.",
            cited=cite(near),
            context=context,
            amounts={"expected": net, "actual": closest.amount, "difference": diff},
            bank_account_id=accounts[closest.statement.account_id].id,
            document_id=closest.statement.document_id,
            match_outcome=Outcome.NEAR,
            **common,
        )

    searched_accounts = sorted(
        {accounts[s.account_id] for s in prep.searched},
        key=lambda a: (a.ending, a.label or "", str(a.id)),
    )
    endings = ", ".join(a.ending + (f" ({a.label})" if a.label else "") for a in searched_accounts)
    plural = "accounts ending" if len(searched_accounts) > 1 else "account ending"
    return _finding(
        Rule.SALARY_CREDIT,
        Status.DISCREPANCY,
        f"{p.month} salary: no credit found",
        f"No credit within {NEAR_PCT}% of {money(net)} {window} in {plural} {endings}.",
        cited=base_cited,
        context=context,
        amounts={"expected": net},
        match_outcome=Outcome.NONE,
        **common,
    )


# --- run --------------------------------------------------------------------------------------


def run(
    payslips: Sequence[rules.Payslip],
    statements: Sequence[Statement],
    accounts: dict[uuid.UUID, Account],
    names: dict[uuid.UUID, str],
) -> list[Finding]:
    """Every bank check. A case without a confirmed statement gives no bank findings at all
    (no statements to check, and nothing to look for salaries in)."""
    if not statements:
        return []
    findings: list[Finding] = []
    ordered = sorted(statements, key=lambda s: s.sort_key)
    for s in ordered:
        findings.extend(check_integrity(s, accounts[s.account_id]))
    for account in accounts.values():
        mine = [s for s in ordered if s.account_id == account.id]
        findings.extend(check_gaps(account, mine))
    slips = sorted(payslips, key=lambda p: (p.pay_date or date.min, str(p.document_id)))
    findings.extend(check_salary_credits(slips, ordered, accounts, names))
    return findings
