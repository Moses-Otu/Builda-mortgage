"""Write a SYNTHETIC payslip PDF for local demos of the review screen. Never real data.

Usage, from backend/:

    uv run python scripts/make_synthetic_payslip.py [classic|earnings|two-pages] [out.pdf]

The PDF carries the layout the synthetic extraction provider reads, so it can be uploaded
to a local stack and extracted. Uses the test generator (needs the dev dependencies).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.synthetic_docs import PAYSLIPS


def main() -> int:
    layout = sys.argv[1] if len(sys.argv) > 1 else "classic"
    if layout not in PAYSLIPS:
        print(f"Unknown layout {layout!r}; choose from {', '.join(PAYSLIPS)}", file=sys.stderr)
        return 2
    out = Path(sys.argv[2] if len(sys.argv) > 2 else f"synthetic-payslip-{layout}.pdf")
    document, _ = PAYSLIPS[layout]()
    out.write_bytes(document.pdf())
    print(f"Wrote {out} (SYNTHETIC test document {document.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
