"""Test package. Imported by pytest before any conftest or test module, and so before meip.

A developer's ``backend/.env`` (the README's local setup) must not leak into tests, including
the subprocesses some tests start (they inherit this environment)."""

import os

os.environ["MEIP_ENV_FILE"] = ""
