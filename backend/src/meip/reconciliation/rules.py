"""The payslip checks (rule set ``payslip-checks/1``): pure functions over plain data.

No database, no HTTP. The service loads a case's current reviewed payslips and declared
figures into the data classes below and calls :func:`run`. Each check returns findings with
one of three statuses: ``passed``, ``discrepancy`` (values, difference, both sources cited) or
``not_run`` (always with a plain reason). The checks never decide anything about the case.

Changing any value in :data:`PARAMETERS` or any rule's behaviour means a new
:data:`RULESET_VERSION`. The 2 % declared-basic threshold is an initial product assumption for
highlighting differences, not a regulatory or lender standard.
"""

from __future__ import annotations

import calendar
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

RULESET_VERSION = "payslip-checks/1"

PARAMETERS: dict[str, Any] = {
    "arithmetic_allowance_pence": 1,
    "ytd_allowance_pence": 100,
    "period_alignment_allowance_days": 3,
    # Initial product assumption, not a regulatory or lender standard.
    "declared_basic_threshold_pct": "2.0",
    "adjustment_markers": [
        "arrears",
        "back pay",
        "backpay",
        "backdated",
        "adjustment",
        "correction",
        "refund",
        "rebate",
        "previous employment",
        "p45",
    ],
    "part_period_markers": ["unpaid", "pro rata", "prorata", "pro-rata", "part period"],
    "tax_year_start": "04-06",
}

ALLOWANCE_ARITH = int(PARAMETERS["arithmetic_allowance_pence"])
ALLOWANCE_YTD = int(PARAMETERS["ytd_allowance_pence"])
ALIGN_DAYS = int(PARAMETERS["period_alignment_allowance_days"])
DECLARED_PCT = Decimal(PARAMETERS["declared_basic_threshold_pct"])
ADJUSTMENT_MARKERS: tuple[str, ...] = tuple(PARAMETERS["adjustment_markers"])
PART_PERIOD_MARKERS: tuple[str, ...] = tuple(PARAMETERS["part_period_markers"])

PER_YEAR = {
    "weekly": 52,
    "fortnightly": 26,
    "four_weekly": 13,
    "monthly": 12,
    "quarterly": 4,
    "annual": 1,
}
PERIOD_DAYS = {"weekly": 7, "fortnightly": 14, "four_weekly": 28}


class Rule(StrEnum):
    PAY_ARITH = "PAY-ARITH"
    PERIOD_GAP = "PERIOD-GAP"
    PERIOD_DUP = "PERIOD-DUP"
    YTD_GROSS = "YTD-GROSS"
    YTD_TAX = "YTD-TAX"
    DECLARED_BASIC = "DECLARED-BASIC"


class Status(StrEnum):
    PASSED = "passed"
    DISCREPANCY = "discrepancy"
    NOT_RUN = "not_run"


# --- inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Figure:
    """A current document figure (a ``document`` observation) and the line it came from."""

    observation_id: uuid.UUID
    field_review_id: uuid.UUID
    ordinal: int
    label: str
    measure: str
    qualifier: str | None
    amount: int
    frequency: str | None
    period_start: date | None
    period_end: date | None
    as_of: date | None


@dataclass(frozen=True)
class Reviewed:
    """A reviewed descriptive field (pay date, period dates, total deductions), as accepted or
    corrected. Rejected fields are never loaded."""

    field_review_id: uuid.UUID
    ordinal: int
    label: str
    code: str
    decision: str
    value: int | date | str


@dataclass(frozen=True)
class Payslip:
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    income_source_id: uuid.UUID
    applicant_id: uuid.UUID
    frequency: str
    figures: tuple[Figure, ...] = ()
    reviewed: tuple[Reviewed, ...] = ()
    line_labels: tuple[str, ...] = ()

    def field(self, code: str) -> Reviewed | None:
        return next((r for r in self.reviewed if r.code == code), None)

    def date_of(self, code: str) -> date | None:
        r = self.field(code)
        return r.value if r is not None and isinstance(r.value, date) else None

    @property
    def pay_date(self) -> date | None:
        return self.date_of("pay_date")

    @property
    def period_start(self) -> date | None:
        return self.date_of("pay_period_start")

    @property
    def period_end(self) -> date | None:
        return self.date_of("pay_period_end")

    @property
    def sort_date(self) -> date:
        return self.period_end or self.pay_date or date.min

    def figures_for(self, measure: str) -> list[Figure]:
        return [f for f in self.figures if f.measure == measure]

    def amount(self, measure: str) -> int | None:
        found = self.figures_for(measure)
        return sum(f.amount for f in found) if found else None

    @property
    def month(self) -> str:
        """'June 2026', from the period end or the pay date."""
        d = self.period_end or self.pay_date
        return f"{d:%B %Y}" if d else "undated"

    @property
    def name(self) -> str:
        """'June 2026 payslip', from the period end or the pay date."""
        d = self.period_end or self.pay_date
        return f"{d:%B %Y} payslip" if d else "payslip"

    def markers(self, words: Iterable[str]) -> list[str]:
        lowered = [(label, label.lower()) for label in self.line_labels]
        return [label for label, low in lowered if any(w in low for w in words)]


@dataclass(frozen=True)
class Declared:
    observation_id: uuid.UUID
    income_source_id: uuid.UUID
    measure: str
    amount: int
    frequency: str


Cite = Figure | Reviewed | Declared


@dataclass(frozen=True)
class Cited:
    """One cited input, with the payslip it came from (None for a declared figure)."""

    item: Cite
    payslip: Payslip | None


@dataclass(frozen=True)
class Finding:
    rule: Rule
    status: Status
    title: str
    message: str
    income_source_id: uuid.UUID
    applicant_id: uuid.UUID
    cited: tuple[Cited, ...] = ()
    amounts: dict[str, int] = field(default_factory=dict)
    key: str = ""


# --- helpers ----------------------------------------------------------------------------------


def money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    pounds, pence = divmod(abs(minor), 100)
    return f"{sign}£{pounds:,}.{pence:02d}"


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def tax_year_of(d: date) -> int:
    """The calendar year a UK tax year starts in (6 April to 5 April)."""
    return d.year if d >= date(d.year, 4, 6) else d.year - 1


def _is_month_end(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def _next_month_length(d: date) -> int:
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return calendar.monthrange(year, month)[1]


def _period_length(frequency: str, after: date) -> int:
    if frequency == "monthly":
        return _next_month_length(after)
    return PERIOD_DAYS.get(frequency, 0)


def _cite(payslip: Payslip | None, *items: Cite | None) -> list[Cited]:
    return [Cited(i, payslip) for i in items if i is not None]


def _canonical(cited: Cited) -> dict[str, Any]:
    item = cited.item
    out: dict[str, Any]
    if isinstance(item, Figure):
        out = {
            "observation_id": item.observation_id,
            "field_review_id": item.field_review_id,
            "measure": item.measure,
            "qualifier": item.qualifier,
            "amount": item.amount,
            "frequency": item.frequency,
            "period_start": item.period_start,
            "period_end": item.period_end,
            "as_of": item.as_of,
        }
    elif isinstance(item, Reviewed):
        out = {
            "field_review_id": item.field_review_id,
            "code": item.code,
            "decision": item.decision,
            "value": item.value,
        }
    else:
        out = {
            "observation_id": item.observation_id,
            "measure": item.measure,
            "amount": item.amount,
            "frequency": item.frequency,
        }
    if cited.payslip is not None:
        p = cited.payslip
        out["document_version_id"] = p.document_version_id
        out["pay_date"] = p.pay_date
        out["period"] = [p.period_start, p.period_end]
        out["review_frequency"] = p.frequency
    return out


def fingerprint(rule: Rule, income_source_id: uuid.UUID, cited: Sequence[Cited]) -> str:
    """SHA-256 over the rule-set version, rule, parameters and the identity, value and period
    of every cited input. Any change to any of them gives a different key."""
    document = {
        "ruleset": RULESET_VERSION,
        "rule": rule.value,
        "parameters": PARAMETERS,
        "income_source_id": income_source_id,
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
    source: tuple[uuid.UUID, uuid.UUID],
    cited: Sequence[Cited],
    amounts: dict[str, int] | None = None,
) -> Finding:
    income_source_id, applicant_id = source
    return Finding(
        rule=rule,
        status=status,
        title=title,
        message=message,
        income_source_id=income_source_id,
        applicant_id=applicant_id,
        cited=tuple(cited),
        amounts=amounts or {},
        key=fingerprint(rule, income_source_id, cited),
    )


# --- PAY-ARITH --------------------------------------------------------------------------------


def check_arithmetic(p: Payslip, source: tuple[uuid.UUID, uuid.UUID]) -> Finding:
    gross, net = p.figures_for("gross_pay"), p.figures_for("net_pay")
    deductions = p.field("total_deductions")
    missing = [
        name
        for name, present in (
            ("gross pay", bool(gross)),
            ("total deductions", deductions is not None and isinstance(deductions.value, int)),
            ("net pay", bool(net)),
        )
        if not present
    ]
    cited = _cite(p, *gross, deductions, *net)
    if missing:
        return _finding(
            Rule.PAY_ARITH,
            Status.NOT_RUN,
            f"Payslip arithmetic — {p.name}",
            f"Could not run: the reviewed payslip has no {', '.join(missing)}.",
            source,
            cited,
        )
    d = deductions.value if deductions is not None and isinstance(deductions.value, int) else 0
    g, n = sum(f.amount for f in gross), sum(f.amount for f in net)
    difference = (g - d) - n
    amounts = {
        "expected": g - d,
        "actual": n,
        "difference": difference,
        "allowance": ALLOWANCE_ARITH,
    }
    if abs(difference) <= ALLOWANCE_ARITH:
        return _finding(
            Rule.PAY_ARITH,
            Status.PASSED,
            f"Payslip adds up — {p.name}",
            f"Gross {money(g)} less deductions {money(d)} equals net {money(n)}.",
            source,
            cited,
            amounts,
        )
    return _finding(
        Rule.PAY_ARITH,
        Status.DISCREPANCY,
        f"Payslip doesn't add up — {p.name}",
        f"Gross pay {money(g)} less total deductions {money(d)} is {money(g - d)}, but net pay "
        f"is {money(n)}. Difference {money(abs(difference))}.",
        source,
        cited,
        amounts,
    )


# --- PERIOD-GAP and PERIOD-DUP ------------------------------------------------------------------


class Pair(StrEnum):
    CONSECUTIVE = "consecutive"
    GAP = "gap"
    OVERLAP = "overlap"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Coverage:
    kind: Pair
    reason: str = ""
    missing_from: date | None = None
    missing_to: date | None = None
    missing_periods: int = 0


def coverage(a: Payslip, b: Payslip, frequency: str) -> Coverage:
    """How two consecutive payslips (``a`` before ``b``) cover time, from their pay periods.
    Pay dates are never used to judge coverage (assumption A1)."""
    a_start, a_end, b_start, b_end = a.period_start, a.period_end, b.period_start, b.period_end
    if a_end is None or b_end is None:
        if a.pay_date is not None and a.pay_date == b.pay_date and a_end is None and b_end is None:
            return Coverage(Pair.OVERLAP, "both payslips have the same pay date")
        return Coverage(Pair.UNKNOWN, "the payslips don't show their pay periods")
    if a_end == b_end:
        return Coverage(Pair.OVERLAP, f"both payslips' periods end on {_day(a_end)}")

    if a_start is not None and b_start is not None:
        if b_start <= a_end:
            return Coverage(
                Pair.OVERLAP,
                f"{a.name} runs to {_day(a_end)} and {b.name} starts on {_day(b_start)}",
            )
        uncovered = (b_start - a_end).days - 1
        if uncovered <= ALIGN_DAYS:
            return Coverage(Pair.CONSECUTIVE)
        whole = _period_length(frequency, a_end)
        if whole and uncovered >= whole - ALIGN_DAYS:
            first, last = a_end + timedelta(days=1), b_start - timedelta(days=1)
            periods = max(1, round(uncovered / whole))
            return Coverage(Pair.GAP, missing_from=first, missing_to=last, missing_periods=periods)
        return Coverage(
            Pair.UNKNOWN,
            f"the periods don't line up: {uncovered} days, {_day(a_end + timedelta(days=1))} to "
            f"{_day(b_start - timedelta(days=1))}, aren't covered by either payslip",
        )

    # Only period ends: count whole periods between them.
    if frequency == "monthly":
        steps = (b_end.year * 12 + b_end.month) - (a_end.year * 12 + a_end.month)
        aligned = (_is_month_end(a_end) and _is_month_end(b_end)) or abs(
            b_end.day - a_end.day
        ) <= ALIGN_DAYS
    elif frequency in PERIOD_DAYS:
        length = PERIOD_DAYS[frequency]
        days = (b_end - a_end).days
        steps = round(days / length)
        aligned = abs(days - steps * length) <= ALIGN_DAYS
    else:
        return Coverage(Pair.UNKNOWN, f"periods can't be compared for {frequency} pay")
    if not aligned or steps < 0:
        return Coverage(Pair.UNKNOWN, "the period end dates are irregular")
    if steps == 0:
        return Coverage(Pair.OVERLAP, f"both payslips' periods end in {b_end:%B %Y}")
    if steps == 1:
        return Coverage(Pair.CONSECUTIVE)
    return Coverage(
        Pair.GAP,
        missing_from=a_end + timedelta(days=1),
        missing_to=None,
        missing_periods=steps - 1,
    )


def _period_cites(p: Payslip) -> list[Cited]:
    return _cite(p, p.field("pay_period_start"), p.field("pay_period_end"), p.field("pay_date"))


def check_periods(
    payslips: Sequence[Payslip], source: tuple[uuid.UUID, uuid.UUID]
) -> tuple[list[Finding], dict[tuple[uuid.UUID, uuid.UUID], Coverage]]:
    """``PERIOD-GAP`` and ``PERIOD-DUP`` over one income source's payslips (in order); also
    returns each consecutive pair's coverage, which the year-to-date checks rely on."""
    pairs: dict[tuple[uuid.UUID, uuid.UUID], Coverage] = {}
    if len(payslips) < 2:
        return [], pairs
    frequencies = {p.frequency for p in payslips}
    all_cites = [c for p in payslips for c in _period_cites(p)]
    if len(frequencies) > 1:
        reason = (
            f"the payslips are reviewed at different frequencies ({', '.join(sorted(frequencies))})"
        )
        return [
            _finding(rule, Status.NOT_RUN, title, f"Could not run: {reason}.", source, all_cites)
            for rule, title in (
                (Rule.PERIOD_GAP, "Pay periods"),
                (Rule.PERIOD_DUP, "Periods paid twice"),
            )
        ], {
            (a.document_id, b.document_id): Coverage(Pair.UNKNOWN, reason)
            for a, b in itertools.pairwise(payslips)
        }
    frequency = next(iter(frequencies))
    findings: list[Finding] = []
    gap_problems = dup_problems = 0
    for a, b in itertools.pairwise(payslips):
        cov = coverage(a, b, frequency)
        pairs[(a.document_id, b.document_id)] = cov
        cited = _period_cites(a) + _period_cites(b)
        if cov.kind is Pair.GAP:
            gap_problems += 1
            if cov.missing_to is not None:
                span = (
                    f"{_day(cov.missing_from)} to {_day(cov.missing_to)}"
                    if cov.missing_from
                    else ""
                )
            else:
                span = f"from {_day(cov.missing_from)}" if cov.missing_from else ""
            count = cov.missing_periods
            findings.append(
                _finding(
                    Rule.PERIOD_GAP,
                    Status.DISCREPANCY,
                    f"Pay period not covered — {span}",
                    f"The {a.name} covers up to {_day(a.period_end or a.sort_date)}; "
                    f"the next, the {b.name}, "
                    + (
                        f"starts {_day(b.period_start)}"
                        if b.period_start
                        else f"ends {_day(b.period_end or b.sort_date)}"
                    )
                    + f". About {count} {frequency.replace('_', '-')} period"
                    + ("s" if count != 1 else "")
                    + " with no payslip.",
                    source,
                    cited,
                )
            )
        elif cov.kind is Pair.OVERLAP:
            dup_problems += 1
            findings.append(
                _finding(
                    Rule.PERIOD_DUP,
                    Status.DISCREPANCY,
                    f"Period paid twice? — {b.name}",
                    f"Two payslips cover the same time: {cov.reason}.",
                    source,
                    cited,
                )
            )
        elif cov.kind is Pair.UNKNOWN:
            gap_problems += 1
            findings.append(
                _finding(
                    Rule.PERIOD_GAP,
                    Status.NOT_RUN,
                    f"Pay periods — {a.name} to {b.name}",
                    f"Could not run: {cov.reason}.",
                    source,
                    cited,
                )
            )
    first, last = payslips[0], payslips[-1]
    span = f"{first.name.replace(' payslip', '')} to {last.name.replace(' payslip', '')}"
    if gap_problems == 0:
        findings.append(
            _finding(
                Rule.PERIOD_GAP,
                Status.PASSED,
                f"Pay periods covered — {span}",
                f"{len(payslips)} {frequency.replace('_', '-')} payslips with no uncovered period.",
                source,
                all_cites,
            )
        )
    if dup_problems == 0:
        findings.append(
            _finding(
                Rule.PERIOD_DUP,
                Status.PASSED,
                f"No period paid twice — {span}",
                f"None of the {len(payslips)} payslips cover the same period.",
                source,
                all_cites,
            )
        )
    return findings, pairs


# --- YTD-GROSS and YTD-TAX ------------------------------------------------------------------------


def _first_period_of_tax_year(p: Payslip) -> bool:
    """Whether the payslip is in tax period 1. PAYE tax periods follow the **pay date**: month 1
    is paid 6 April to 5 May; week 1 (or the first fortnight or four weeks) starts 6 April.
    Without a pay date, it can't be told (so the payslip is not treated as the first)."""
    if p.pay_date is None:
        return False
    start = date(tax_year_of(p.pay_date), 4, 6)
    if p.frequency == "monthly":
        end = date(start.year, 5, 5)
    elif p.frequency in PERIOD_DAYS:
        end = start + timedelta(days=PERIOD_DAYS[p.frequency] - 1)
    else:
        return False
    return start <= p.pay_date <= end


def check_year_to_date(
    rule: Rule,
    payslips: Sequence[Payslip],
    pairs: dict[tuple[uuid.UUID, uuid.UUID], Coverage],
    source: tuple[uuid.UUID, uuid.UUID],
) -> list[Finding]:
    ytd_measure, period_measure = (
        ("ytd_gross_pay", "gross_pay")
        if rule is Rule.YTD_GROSS
        else ("ytd_income_tax", "income_tax")
    )
    what = "gross pay" if rule is Rule.YTD_GROSS else "tax"
    title = "Year to date adds up" if rule is Rule.YTD_GROSS else "Tax to date adds up"
    broken = (
        "Year to date doesn't add up" if rule is Rule.YTD_GROSS else "Tax to date doesn't add up"
    )
    neutral = "Year to date (gross pay)" if rule is Rule.YTD_GROSS else "Tax to date"
    findings: list[Finding] = []
    checked: list[Payslip] = []
    unchecked_first: list[str] = []

    def not_run(p: Payslip, reason: str, cited: list[Cited]) -> None:
        findings.append(
            _finding(
                rule,
                Status.NOT_RUN,
                f"{neutral} — {p.name}",
                f"Could not run: {reason}.",
                source,
                cited,
            )
        )

    for i, b in enumerate(payslips):
        b_ytd, b_period = b.figures_for(ytd_measure), b.figures_for(period_measure)
        cited_b = _cite(b, *b_ytd, *b_period)
        if not b_ytd or not b_period:
            if b_ytd or b_period:  # one without the other: say so; neither: nothing to check
                not_run(
                    b,
                    f"the payslip has no reviewed {what} "
                    + ("to date" if not b_ytd else "for the period"),
                    cited_b,
                )
            continue
        if rule is Rule.YTD_GROSS and any("taxable" in f.label.lower() for f in b_ytd):
            not_run(
                b,
                f"the year-to-date figure is “{b_ytd[0].label}”: taxable pay excludes pension and "
                "salary-sacrifice deductions, so it can't be compared with gross pay",
                cited_b,
            )
            continue
        ytd, period_amount = sum(f.amount for f in b_ytd), sum(f.amount for f in b_period)
        a = payslips[i - 1] if i > 0 else None
        anchor_b = b.pay_date or b.period_end
        same_year = (
            a is not None
            and anchor_b is not None
            and (a.pay_date or a.period_end) is not None
            and tax_year_of(a.pay_date or a.period_end) == tax_year_of(anchor_b)  # type: ignore[arg-type]
        )
        if a is not None and same_year:
            cov = pairs.get((a.document_id, b.document_id), Coverage(Pair.UNKNOWN, "unknown"))
            a_ytd = a.figures_for(ytd_measure)
            cited = _cite(a, *a_ytd) + cited_b
            if cov.kind is Pair.GAP:
                not_run(b, f"a pay period is missing between the {a.name} and the {b.name}", cited)
                continue
            if cov.kind is Pair.OVERLAP:
                not_run(b, f"the {a.name} and the {b.name} cover the same period", cited)
                continue
            if cov.kind is Pair.UNKNOWN:
                not_run(
                    b,
                    f"it can't be told whether the {a.name} and the {b.name} are "
                    f"consecutive ({cov.reason})",
                    cited,
                )
                continue
            if not a_ytd:
                not_run(b, f"the {a.name} has no reviewed {what} to date", cited)
                continue
            previous = sum(f.amount for f in a_ytd)
            if ytd < previous:
                not_run(
                    b,
                    f"{what} to date fell from {money(previous)} to {money(ytd)}: a new tax "
                    "year, a new employer or an employer correction. Check it by eye",
                    cited,
                )
                continue
            increase = ytd - previous
            difference = increase - period_amount
            amounts = {
                "expected": period_amount,
                "actual": increase,
                "difference": difference,
                "allowance": ALLOWANCE_YTD,
            }
            if abs(difference) <= ALLOWANCE_YTD:
                checked.append(b)
                continue
            explained = a.markers(ADJUSTMENT_MARKERS) + b.markers(ADJUSTMENT_MARKERS)
            if explained:
                not_run(
                    b,
                    f"{what} to date rose by {money(increase)} against "
                    f"{money(period_amount)} for the period, which may be explained by "
                    f"“{explained[0]}”. Check it by eye",
                    cited,
                )
                continue
            findings.append(
                _finding(
                    rule,
                    Status.DISCREPANCY,
                    f"{broken} — {b.name}",
                    f"{what.capitalize()} to date went from {money(previous)} ({a.month}) to "
                    f"{money(ytd)} ({b.month}), an increase of {money(increase)}; {b.month}'s "
                    f"{what} is {money(period_amount)}. Difference {money(abs(difference))} "
                    f"(allowance {money(ALLOWANCE_YTD)}).",
                    source,
                    cited,
                    amounts,
                )
            )
            continue
        # No earlier payslip from this tax year in the set.
        if _first_period_of_tax_year(b):
            difference = ytd - period_amount
            amounts = {
                "expected": period_amount,
                "actual": ytd,
                "difference": difference,
                "allowance": ALLOWANCE_YTD,
            }
            if abs(difference) <= ALLOWANCE_YTD:
                checked.append(b)
            elif b.markers(ADJUSTMENT_MARKERS):
                not_run(
                    b,
                    f"the first payslip of the tax year shows {money(ytd)} to date against "
                    f"{money(period_amount)} for the period, which may be explained by "
                    f"“{b.markers(ADJUSTMENT_MARKERS)[0]}”. Check it by eye",
                    cited_b,
                )
            else:
                findings.append(
                    _finding(
                        rule,
                        Status.DISCREPANCY,
                        f"{broken} — {b.name}",
                        f"This is the first pay period of the tax year, so {what} to date should "
                        f"equal the period's {what} ({money(period_amount)}); it is {money(ytd)}. "
                        f"Difference {money(abs(difference))}.",
                        source,
                        cited_b,
                        amounts,
                    )
                )
        else:
            unchecked_first.append(b.name)

    if checked:
        note = (
            f" (Nothing to compare the {', '.join(unchecked_first)} with: no earlier "
            "payslip from its tax year.)"
            if unchecked_first
            else ""
        )
        cited = [
            c
            for p in checked
            for c in _cite(p, *p.figures_for(ytd_measure), *p.figures_for(period_measure))
        ]
        findings.append(
            _finding(
                rule,
                Status.PASSED,
                f"{title} — {len(checked)} payslip{'s' if len(checked) != 1 else ''}",
                f"{what.capitalize()} to date rises by each period's {what}, within "
                f"{money(ALLOWANCE_YTD)}.{note}",
                source,
                cited,
            )
        )
    return findings


# --- DECLARED-BASIC ------------------------------------------------------------


def check_declared_basic(
    payslips: Sequence[Payslip],
    declared: Declared | None,
    source: tuple[uuid.UUID, uuid.UUID],
) -> Finding | None:
    title = "Declared basic salary"
    if declared is None:
        if not payslips:
            return None
        return _finding(
            Rule.DECLARED_BASIC,
            Status.NOT_RUN,
            title,
            "Could not run: no declared basic salary for this income source.",
            source,
            [],
        )
    declared_cite = [Cited(declared, None)]
    declared_annual = declared.amount * PER_YEAR.get(declared.frequency, 0)
    with_basic = [p for p in payslips if p.figures_for("basic_pay")]
    if not payslips:
        return _finding(
            Rule.DECLARED_BASIC,
            Status.NOT_RUN,
            title,
            "Could not run: no reviewed payslips yet.",
            source,
            declared_cite,
        )
    if declared_annual <= 0:
        return _finding(
            Rule.DECLARED_BASIC,
            Status.NOT_RUN,
            title,
            "Could not run: the declared basic salary isn't an annual or periodic amount.",
            source,
            declared_cite,
        )
    if not with_basic:
        return _finding(
            Rule.DECLARED_BASIC,
            Status.NOT_RUN,
            title,
            "Could not run: no payslip shows a basic pay line (only total earnings, which can "
            "include overtime, bonus and allowances).",
            source,
            declared_cite,
        )
    latest = with_basic[-1]
    basic = latest.figures_for("basic_pay")
    cited = declared_cite + _cite(latest, *basic)
    part = latest.markers(PART_PERIOD_MARKERS)
    if part:
        return _finding(
            Rule.DECLARED_BASIC,
            Status.NOT_RUN,
            title,
            f"Could not run: the {latest.name} has a “{part[0]}” line, so its basic pay may not be "
            "a full period's.",
            source,
            cited,
        )
    per_year = PER_YEAR.get(latest.frequency)
    if per_year is None:
        return _finding(
            Rule.DECLARED_BASIC,
            Status.NOT_RUN,
            title,
            f"Could not run: {latest.frequency} pay can't be annualised.",
            source,
            cited,
        )
    period_basic = sum(f.amount for f in basic)
    evidenced = period_basic * per_year
    amounts_seen = [p.amount("basic_pay") or 0 for p in with_basic]
    low, high = min(amounts_seen), max(amounts_seen)
    spread = (
        f"Basic pay was {money(low)} on all {len(with_basic)} payslips"
        if low == high
        else f"Basic pay varied from {money(low)} to {money(high)} across "
        f"{len(with_basic)} payslips"
    )
    if len(with_basic) == 1:
        spread = f"Basic pay was {money(low)} on the only payslip with a basic line"
    difference = evidenced - declared_annual
    amounts = {"expected": declared_annual, "actual": evidenced, "difference": difference}
    basis = (
        f"Declared {money(declared_annual)} a year; the {latest.name} shows {money(period_basic)} "
        f"{latest.frequency.replace('_', '-')}, {money(evidenced)} a year. {spread}."
    )
    if abs(difference) * 100 <= declared_annual * DECLARED_PCT:
        return _finding(Rule.DECLARED_BASIC, Status.PASSED, title, basis, source, cited, amounts)
    pct = (Decimal(abs(difference)) * 100 / Decimal(declared_annual)).quantize(Decimal("0.1"))
    direction = "lower" if difference < 0 else "higher"
    return _finding(
        Rule.DECLARED_BASIC,
        Status.DISCREPANCY,
        f"Declared basic salary differs from the payslips — {pct} % {direction}",
        f"{basis} Difference {money(abs(difference))} a year, more than "
        f"{PARAMETERS['declared_basic_threshold_pct'].rstrip('0').rstrip('.')} % of the declared "
        "figure (a threshold for highlighting differences, not a lender or regulatory standard).",
        source,
        cited,
        amounts,
    )


# --- the rule set --------------------------------------------------------------


def run(
    payslips: Iterable[Payslip],
    declared: Iterable[Declared],
    sources: dict[uuid.UUID, uuid.UUID],
) -> list[Finding]:
    """Run every check for every income source (``sources``: income source -> applicant)."""
    by_source: dict[uuid.UUID, list[Payslip]] = {s: [] for s in sources}
    for p in payslips:
        by_source.setdefault(p.income_source_id, []).append(p)
    declared_by_source: dict[uuid.UUID, Declared] = {}
    for d in declared:
        if d.measure == "basic_pay":
            declared_by_source[d.income_source_id] = d
    findings: list[Finding] = []
    for source_id, slips in by_source.items():
        applicant_id = sources.get(source_id) or (slips[0].applicant_id if slips else None)
        if applicant_id is None:
            continue
        source = (source_id, applicant_id)
        slips.sort(key=lambda p: (p.sort_date, p.pay_date or date.min, str(p.document_id)))
        for p in slips:
            findings.append(check_arithmetic(p, source))
        period_findings, pairs = check_periods(slips, source)
        findings.extend(period_findings)
        findings.extend(check_year_to_date(Rule.YTD_GROSS, slips, pairs, source))
        findings.extend(check_year_to_date(Rule.YTD_TAX, slips, pairs, source))
        declared_finding = check_declared_basic(slips, declared_by_source.get(source_id), source)
        if declared_finding is not None:
            findings.append(declared_finding)
    return findings
