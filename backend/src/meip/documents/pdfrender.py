"""Render an untrusted PDF's pages, in a separate, time-limited process (Phase 4).

Same isolation as :mod:`meip.documents.pdfcheck`: the worker writes the PDF to a temporary
file and runs this module with a clean environment (no database URL, no API keys, no storage
credentials). The child writes one PNG per page into a temporary directory and prints one
JSON line describing the pages and their text-layer segments (exact text and boxes from the
PDF itself, when it has a text layer). The parent reads the images and always deletes the
temporary directory, whatever happens.

Coordinates are PDF points with a top-left origin, matching the extraction layout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

LONG_SIDE_PX = 1800  # enough to read small print; keeps each page image modest
MAX_SCALE = 3.0


@dataclass(frozen=True)
class Segment:
    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in points, top-left origin


@dataclass(frozen=True)
class RenderedPage:
    number: int
    width: float
    height: float
    png: bytes
    segments: list[Segment] = field(default_factory=list)


class RenderError(Exception):
    """The PDF couldn't be rendered. The message is a code, never document content."""


def _render(path: str, out_dir: str) -> dict[str, object]:
    import pypdfium2 as pdfium  # imported only in the child process

    document = pdfium.PdfDocument(path)
    pages: list[dict[str, object]] = []
    try:
        for index in range(len(document)):
            page = document[index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            segments = []
            for r in range(textpage.count_rects()):
                left, bottom, right, top = textpage.get_rect(r)
                text = textpage.get_text_bounded(left, bottom, right, top).strip()
                if not text:
                    continue
                x0, x1 = max(0.0, left), min(width, right)
                y0, y1 = max(0.0, height - top), min(height, height - bottom)
                if x1 > x0 and y1 > y0:
                    segments.append({"text": " ".join(text.split()), "box": [x0, y0, x1, y1]})
            scale = min(MAX_SCALE, LONG_SIDE_PX / max(width, height))
            image = page.render(scale=scale).to_pil()
            image.save(Path(out_dir) / f"{index + 1}.png", format="PNG", optimize=True)
            pages.append(
                {"number": index + 1, "width": width, "height": height, "segments": segments}
            )
            textpage.close()
            page.close()
    finally:
        document.close()
    return {"pages": pages}


def render_pdf(pdf: bytes, timeout: float) -> list[RenderedPage]:
    """Render every page in a child process. Raises :class:`RenderError` with a code."""
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
    work = Path(tempfile.mkdtemp(prefix="meip-render-"))
    try:
        source = work / "document.pdf"
        source.write_bytes(pdf)
        try:
            result = subprocess.run(  # noqa: S603 - fixed interpreter and module; paths are ours
                [sys.executable, "-I", "-m", "meip.documents.pdfrender", str(source), str(work)],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RenderError("render_timeout") from None
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            raise RenderError("render_failed") from None
        if "error" in payload:
            raise RenderError(str(payload["error"]))
        out: list[RenderedPage] = []
        for p in payload["pages"]:
            number = int(p["number"])
            out.append(
                RenderedPage(
                    number=number,
                    width=float(p["width"]),
                    height=float(p["height"]),
                    png=(work / f"{number}.png").read_bytes(),
                    segments=[
                        Segment(str(s["text"]), tuple(float(v) for v in s["box"]))  # type: ignore[arg-type]
                        for s in p["segments"]
                    ],
                )
            )
        return out
    finally:
        # Temporary copies of a borrower document never outlive the call.
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        outcome = _render(sys.argv[1], sys.argv[2])
    except Exception:
        outcome = {"error": "render_failed"}
    print(json.dumps(outcome))
