"""Stage 3-B: every case-scoped write takes the case row lock.

Approving the profile relies on it (plan revision 2, section 4.2): once an approval holds the
case lock, no other user write to that case can change the profile until the approval
commits. Every service function that checks ``require_editable`` (the guard of every case
write) must therefore lock the case row in the same function, directly
(``get_case(…, for_update=True)``) or through a helper that does. A new write path that
forgets fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "meip"

GUARDS = {"require_editable", "_require_editable"}
LOCKING_HELPERS = {"_editable_case", "_locked_editable_case"}

# Functions that check editability without locking, and why that's safe.
EXEMPT = {
    # The guards themselves.
    ("cases/service.py", "require_editable"),
    ("cases/service.py", "_require_editable"),
    # A cheap check before the upload body is read; store_upload re-checks under the lock.
    ("documents/service.py", "check_upload_target"),
}


def _calls(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            names.add(
                f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            )
    return names


def _locks(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            for kw in sub.keywords:
                if kw.arg == "for_update" and isinstance(kw.value, ast.Constant) and kw.value.value:
                    return True
    return bool(_calls(node) & LOCKING_HELPERS)


def test_every_editability_check_is_under_the_case_lock() -> None:
    unlocked = []
    checked = 0
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not (_calls(fn) & GUARDS) or (rel, fn.name) in EXEMPT:
                continue
            checked += 1
            if not _locks(fn):
                unlocked.append(f"{rel}:{fn.name}")
    assert checked > 5, "the scan found too few write paths: is SRC right?"
    assert unlocked == [], f"case writes without the case lock: {unlocked}"
