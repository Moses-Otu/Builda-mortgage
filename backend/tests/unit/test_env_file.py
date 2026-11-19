"""The settings' dotenv file: tests must be immune to a developer's backend/.env."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROBE = (
    "from meip.core.config import Settings; s = Settings();"
    "print(s.blob_backend.value, s.documents_enabled)"
)


def _probe(cwd: Path, env_file: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEIP_")}
    env["MEIP_ENV"] = "test"
    env["MEIP_AUTH_MODE"] = "dev"
    if env_file is not None:
        env["MEIP_ENV_FILE"] = env_file
    result = subprocess.run(  # noqa: S603 # fixed interpreter and code
        [sys.executable, "-c", PROBE], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_the_suite_runs_with_the_env_file_disabled() -> None:
    assert os.environ["MEIP_ENV_FILE"] == ""


def test_an_empty_env_file_setting_ignores_a_conflicting_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("MEIP_BLOB_BACKEND=filesystem\nMEIP_DOCUMENTS_ENABLED=true\n")
    default = _probe(tmp_path, None)  # nothing set: .env is read, as for local development
    ignored = _probe(tmp_path, "")
    assert default == "filesystem True"
    assert ignored != default
    assert ignored.endswith("False")
