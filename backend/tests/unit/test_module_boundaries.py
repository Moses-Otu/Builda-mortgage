"""Module dependency rules, checked by parsing imports (standard-library ``ast`` only).

* Domain modules may import only the modules listed for them. In particular the financial
  domain must never depend on document storage or extraction (Phase 2 adds those modules;
  they will not appear in its allowlist).
* Only ``api`` (and the app factory) may import FastAPI/Starlette, so domain services stay
  usable by background workers that have no HTTP request.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "meip"

ALLOWED: dict[str, set[str]] = {
    "core": {"core"},
    "auth": {"core", "auth"},
    "audit": {"core", "audit", "tenancy"},
    "tenancy": {"core", "tenancy", "audit", "auth", "firm"},
    "firm": {"core", "firm", "audit", "tenancy"},
    "cases": {"core", "cases", "applicants", "audit", "tenancy"},
    "applicants": {"core", "applicants", "cases", "audit", "tenancy"},
    "financial": {"core", "financial", "applicants", "cases", "audit", "tenancy"},
    # documents may save reviewed figures through the financial service; never the reverse.
    # documents may also import the model registry (meip.models) so the standalone worker can
    # resolve foreign keys; it holds no services.
    "documents": {
        "core",
        "documents",
        "applicants",
        "cases",
        "audit",
        "tenancy",
        "financial",
        "models",
    },
}
HTTP_FRAMEWORKS = {"fastapi", "starlette"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _package_files(package: str) -> list[Path]:
    return sorted((SRC / package).rglob("*.py"))


def test_every_domain_package_has_a_rule() -> None:
    packages = {p.name for p in SRC.iterdir() if p.is_dir() and not p.name.startswith("_")}
    assert packages - {"api"} == set(ALLOWED)


@pytest.mark.parametrize("package", sorted(ALLOWED))
def test_domain_imports_follow_the_rules(package: str) -> None:
    violations = []
    for path in _package_files(package):
        for name in _imports(path):
            parts = name.split(".")
            if parts[0] == "meip" and len(parts) > 1 and parts[1] not in ALLOWED[package]:
                violations.append(f"{path.relative_to(SRC)} imports {name}")
            if parts[0] in HTTP_FRAMEWORKS:
                violations.append(f"{path.relative_to(SRC)} imports {name} (HTTP framework)")
    assert violations == []


def test_the_worker_can_reach_no_user_facing_service() -> None:
    """The worker shares the API's database role (an MVP compromise), so its reach is limited
    in code instead: it imports no case, applicant, financial, firm, member or document
    upload service, and no HTTP framework. It can only run the ingest job."""
    allowed = {
        "meip.audit",
        "meip.audit.service",
        "meip.core.config",
        "meip.core.db",
        "meip.core.logging",
        "meip.documents.extraction",
        "meip.documents.models",
        "meip.documents.pdfcheck",
        "meip.documents.storage",
        # The model registry only (no services): lets the standalone worker resolve foreign keys.
        "meip.models",
        "meip.tenancy.context",
        "meip.tenancy.permissions",
    }
    imported = {n for n in _imports(SRC / "documents" / "processing.py") if n.startswith("meip")}
    assert imported <= allowed, imported - allowed
