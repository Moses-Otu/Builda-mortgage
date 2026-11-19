"""Open an untrusted PDF in a separate, time-limited process.

The parent (the worker) writes the PDF to a temporary file and runs this module with a clean
environment: no database URL, no storage credentials. The child prints one JSON line:
``{"pages": n}`` or ``{"error": "<code>"}``. A crash, hang or exotic exception in PDFium can
therefore never affect the worker's database transaction, and a timeout kills the child.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_PAGES = 10

# PDFium error codes (FPDF_ERR_*): 4 = a password is needed (encrypted).
_PASSWORD_ERROR = 4


@dataclass(frozen=True)
class PdfCheck:
    pages: int | None = None
    error: str | None = None


def _inspect(path: str) -> dict[str, object]:
    import pypdfium2 as pdfium  # imported only in the child process

    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        code = "encrypted_pdf" if getattr(exc, "err_code", None) == _PASSWORD_ERROR else None
        return {"error": code or "unreadable_pdf"}
    try:
        pages = len(document)
    finally:
        document.close()
    if pages < 1:
        return {"error": "unreadable_pdf"}
    if pages > MAX_PAGES:
        return {"error": "too_many_pages"}
    return {"pages": pages}


def check_pdf(path: Path, timeout: float) -> PdfCheck:
    """Run the check in a child process. Never raises for a bad PDF; returns a code."""
    # Only what Python needs to start; nothing from MEIP_* reaches the child.
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
    try:
        result = subprocess.run(  # noqa: S603 - fixed interpreter and module, path is ours
            [sys.executable, "-I", "-m", "meip.documents.pdfcheck", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return PdfCheck(error="timeout")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return PdfCheck(error="unreadable_pdf")  # the child crashed or printed nothing
    if "pages" in payload:
        return PdfCheck(pages=int(payload["pages"]))
    return PdfCheck(error=str(payload.get("error", "unreadable_pdf")))


if __name__ == "__main__":
    try:
        outcome = _inspect(sys.argv[1])
    except Exception:
        outcome = {"error": "unreadable_pdf"}
    print(json.dumps(outcome))
