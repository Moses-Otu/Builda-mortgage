"""Measure vocabulary, qualifiers and the application-enforced applicability invariant."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from meip.core.errors import BusinessRuleError
from meip.financial.measures import (
    MEASURE_APPLICABILITY,
    MEASURES,
    QUALIFIER_INDEX,
    QUALIFIERS,
    CommitmentKind,
    ExpenditureCategory,
    IncomeKind,
    SubjectKind,
    ensure_applicable,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "meip"
_KINDS = {
    SubjectKind.INCOME_SOURCE: {k.value for k in IncomeKind},
    SubjectKind.COMMITMENT: {k.value for k in CommitmentKind},
    SubjectKind.EXPENDITURE: {k.value for k in ExpenditureCategory},
}


def test_every_measure_has_an_applicability_rule() -> None:
    assert {(m.code, m.subject_kind) for m in MEASURES} == set(MEASURE_APPLICABILITY)


def test_applicability_only_names_real_kinds() -> None:
    for (code, subject), kinds in MEASURE_APPLICABILITY.items():
        assert kinds, code
        assert {str(k) for k in kinds} <= _KINDS[subject], code


def test_qualified_measures_and_qualifiers_agree() -> None:
    qualified = {(m.code, m.subject_kind) for m in MEASURES if m.qualifier_required}
    assert qualified == set(QUALIFIER_INDEX)
    assert {(q.measure, q.subject_kind) for q in QUALIFIERS} == qualified
    for codes in QUALIFIER_INDEX.values():
        assert {"other", "unspecified"} <= codes


def test_overtime_premium_is_not_an_allowance() -> None:
    allowance = QUALIFIER_INDEX[("allowance", SubjectKind.INCOME_SOURCE)]
    assert "overtime_premium" not in allowance


@pytest.mark.parametrize(
    ("measure", "subject", "kind", "ok"),
    [
        ("basic_pay", SubjectKind.INCOME_SOURCE, "employment", True),
        ("basic_pay", SubjectKind.INCOME_SOURCE, "property_rental", False),
        ("gross_rent", SubjectKind.INCOME_SOURCE, "other", True),  # catch-all kind
        ("credit_limit", SubjectKind.COMMITMENT, "credit_card", True),
        ("credit_limit", SubjectKind.COMMITMENT, "personal_loan", False),
        ("remaining_term", SubjectKind.COMMITMENT, "other", True),
        ("amount", SubjectKind.EXPENDITURE, "childcare", True),
    ],
)
def test_ensure_applicable(measure: str, subject: SubjectKind, kind: str, ok: bool) -> None:
    if ok:
        ensure_applicable(measure, subject, kind)
    else:
        with pytest.raises(BusinessRuleError):
            ensure_applicable(measure, subject, kind)


# --- static guard: every write path uses the single validation function -------------------


def _functions(tree: ast.AST) -> list[ast.AsyncFunctionDef | ast.FunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)]


def _calls(node: ast.AST, name: str) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if called == name:
                return True
    return False


def test_observations_are_only_created_through_validated_paths() -> None:
    """Applicability is application-enforced, so every place that constructs a
    FinancialObservation must call ensure_applicable (Phase 2's normaliser included)."""
    creators = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in _functions(tree):
            if _calls(fn, "FinancialObservation"):
                creators.append((path.relative_to(SRC).as_posix(), fn.name, fn))
    assert creators, "expected at least one write path"
    for where, name, fn in creators:
        assert _calls(fn, "ensure_applicable"), f"{where}:{name} skips ensure_applicable"


def test_item_kind_changes_are_validated() -> None:
    tree = ast.parse((SRC / "financial" / "service.py").read_text(encoding="utf-8"))
    functions = {fn.name: fn for fn in _functions(tree)}
    for name in ("update_income_source", "update_commitment"):
        assert _calls(functions[name], "_ensure_kind_change_applicable"), name
    assert _calls(functions["_ensure_kind_change_applicable"], "ensure_applicable")
