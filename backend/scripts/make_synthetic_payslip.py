"""Write SYNTHETIC payslip PDFs for local demos. Never real data.

Usage, from backend/:

    uv run python scripts/make_synthetic_payslip.py [classic|earnings|two-pages] [out.pdf]
    uv run python scripts/make_synthetic_payslip.py series OUTDIR [--months N]
        [--start YYYY-MM] [--basic POUNDS] [--fault-at I] [--faults a,b,...]

``series`` writes consecutive monthly payslips with consistent year-to-date totals, for the
reconciliation checks. Faults (applied at index --fault-at, or at their own index with
fault@index, e.g. --faults ytd_break@1,missing_month@3): missing_month, ytd_break,
arithmetic_error, duplicate_period, adjustment, taxable_label, period_end_only, no_basic,
part_period.

Each PDF carries the layout the synthetic extraction provider reads, so it can be uploaded to
a local stack and extracted. Upload the files exactly as written: a PDF re-saved by a viewer
loses that layout. Uses the test generator (needs the dev dependencies).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.synthetic_docs import PAYSLIPS, SERIES_FAULTS, payslip_series


def _series(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="make_synthetic_payslip.py series")
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--months", type=int, default=4)
    parser.add_argument("--start", default="2026-05", help="first month, YYYY-MM")
    parser.add_argument("--basic", type=float, default=3750.0, help="monthly basic pay, £")
    parser.add_argument("--fault-at", type=int, default=2)
    parser.add_argument(
        "--faults",
        default="",
        help=f"comma-separated, each optionally @index: {', '.join(sorted(SERIES_FAULTS))}",
    )
    args = parser.parse_args(argv)
    year, month = (int(p) for p in args.start.split("-"))
    faults: set[str] = set()
    positions: dict[str, int] = {}
    for item in (f for f in args.faults.split(",") if f):
        name, _, index = item.partition("@")
        if index:
            positions[name] = int(index)
        else:
            faults.add(name)
    slips = payslip_series(
        (year, month),
        args.months,
        round(args.basic * 100),
        faults=frozenset(faults),
        fault_at=args.fault_at,
        positions=positions,
    )
    args.outdir.mkdir(parents=True, exist_ok=True)
    for n, slip in enumerate(slips, start=1):
        out = args.outdir / f"payslip-{slip.period_end:%Y-%m}-{n}.pdf"
        out.write_bytes(slip.document.pdf())
        print(f"Wrote {out} (SYNTHETIC, paid {slip.pay_date:%d %b %Y})")
    return 0


def main() -> int:
    layout = sys.argv[1] if len(sys.argv) > 1 else "classic"
    if layout == "series":
        return _series(sys.argv[2:])
    if layout not in PAYSLIPS:
        print(
            f"Unknown layout {layout!r}; choose from {', '.join(PAYSLIPS)} or series",
            file=sys.stderr,
        )
        return 2
    out = Path(sys.argv[2] if len(sys.argv) > 2 else f"synthetic-payslip-{layout}.pdf")
    document, _ = PAYSLIPS[layout]()
    out.write_bytes(document.pdf())
    print(f"Wrote {out} (SYNTHETIC test document {document.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
