"""OpenAI-backed layout provider (Phase 4): reads real PDFs, including scanned ones.

It implements the existing :class:`~meip.documents.extraction.LayoutProvider` interface, so
classification, field extraction, statement tables, reconciliation and every calculation stay
in the deterministic Python rules. **The model only transcribes**: for each page it returns the
visible text cells, verbatim, with their positions, through a strict JSON schema (structured
outputs). It never computes, totals, normalises or interprets values.

* **Text-layer verification.** Each page's own text layer (exact text and boxes, from PDFium in
  an isolated process) is offered to the model as numbered segments. A cell the model ties to a
  segment takes that segment's exact text and box (``source = "text-layer"``, confidence 1.0).
  A cell with no segment (a scanned page, or text only in an image) keeps the model's
  transcription and approximate box (``source = "model"``, no confidence): provenance shows
  which is which.
* **Privacy.** Requests are sent with ``store: false``. The API key is server-side only. Nothing
  here logs document text, images or responses; errors carry codes only. Rendered page images
  live only in memory and in a temporary directory that is always deleted.
* **Failures.** Timeouts, rate limits and server errors are retried with backoff, then reported
  as :class:`ProviderUnavailableError` (the job is retried later). Malformed or refused
  responses are :class:`~meip.documents.extraction.ProviderError` (the document fails with
  ``extraction_failed``).
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx

from meip.core.config import Settings
from meip.documents.extraction import (
    Box,
    Layout,
    Line,
    Page,
    ProviderError,
    ProviderUnavailableError,
)
from meip.documents.pdfrender import RenderedPage, RenderError, render_pdf

PROMPT_VERSION = "transcribe-cells/1"
RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
MAX_CELLS_PER_PAGE = 600
MAX_TEXT = 300

INSTRUCTIONS = """You transcribe one page of a financial document (a UK payslip or bank statement).
Return every visible piece of text as a separate cell, in reading order.

Rules:
- Copy text exactly as printed, character for character. Do not correct, reformat, translate,
  total, calculate, infer or summarise anything. Never add text that is not on the page.
- A cell is one label, one value, or one table cell. When a label and its value are separated
  horizontally, they are two cells. Each table cell in a row is its own cell.
- Text-layer segments for this page may be listed below, each with an index. When a cell
  corresponds to a segment, set "segment" to that index and copy the segment's text. Otherwise
  set "segment" to null and give your best box.
- Boxes use coordinates from 0 to 1000 across the page width (x) and height (y), origin at the
  top-left. x0 < x1 and y0 < y1.
- Do not include handwriting you cannot read; skip it rather than guess."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cells": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "segment": {"type": ["integer", "null"]},
                    "box": {
                        "type": "object",
                        "properties": {
                            "x0": {"type": "number"},
                            "y0": {"type": "number"},
                            "x1": {"type": "number"},
                            "y1": {"type": "number"},
                        },
                        "required": ["x0", "y0", "x1", "y1"],
                        "additionalProperties": False,
                    },
                },
                "required": ["text", "segment", "box"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cells"],
    "additionalProperties": False,
}


def _hints(page: RenderedPage) -> str:
    if not page.segments:
        return "This page has no text layer (it may be scanned): transcribe from the image."
    rows = []
    for i, s in enumerate(page.segments):
        x0, y0, x1, y1 = s.box
        norm = [
            round(x0 / page.width * 1000),
            round(y0 / page.height * 1000),
            round(x1 / page.width * 1000),
            round(y1 / page.height * 1000),
        ]
        rows.append({"index": i, "text": s.text, "box": norm})
    return "Text-layer segments for this page:\n" + json.dumps(rows, ensure_ascii=False)


def _output_text(body: dict[str, Any]) -> str:
    """The structured JSON text from a Responses API body, or a ProviderError."""
    if body.get("status") not in (None, "completed"):
        raise ProviderError(f"response_{body.get('status')}")
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "refusal":
                raise ProviderError("model_refused")
            if content.get("type") == "output_text":
                return str(content.get("text", ""))
    raise ProviderError("no_output")


def _clamp(v: float, high: float) -> float:
    return max(0.0, min(high, v))


def page_lines(
    page: RenderedPage, cells: list[dict[str, Any]]
) -> tuple[list[Line], list[dict[str, Any]]]:
    """Turn the model's cells into layout lines, verified against the text layer where
    possible. Returns the lines and the raw record kept as provenance."""
    if len(cells) > MAX_CELLS_PER_PAGE:
        raise ProviderError("too_many_cells")
    lines: list[Line] = []
    raw: list[dict[str, Any]] = []
    for cell in cells:
        text = " ".join(str(cell.get("text", "")).split())[:MAX_TEXT]
        segment = cell.get("segment")
        if isinstance(segment, int) and 0 <= segment < len(page.segments):
            s = page.segments[segment]
            box = Box(*s.box)
            text, source, confidence = s.text[:MAX_TEXT], "text-layer", 1.0
        else:
            b = cell.get("box") or {}
            try:
                x0 = _clamp(float(b["x0"]) / 1000 * page.width, page.width)
                y0 = _clamp(float(b["y0"]) / 1000 * page.height, page.height)
                x1 = _clamp(float(b["x1"]) / 1000 * page.width, page.width)
                y1 = _clamp(float(b["y1"]) / 1000 * page.height, page.height)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError("malformed_box") from exc
            if not (x1 > x0 and y1 > y0):
                continue  # an empty or inverted box can't be shown or reviewed: drop it
            box, source, confidence = Box(x0, y0, x1, y1), "model", None
        if not text:
            continue
        lines.append(Line(page=page.number, text=text, box=box, confidence=confidence))
        raw.append(
            {
                "text": text,
                "box": [round(v, 2) for v in (box.x0, box.y0, box.x1, box.y1)],
                "source": source,
            }
        )
    return lines, raw


class OpenAILayoutProvider:
    name = "openai"

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        backoff: float = 1.0,
    ) -> None:
        if settings.openai_api_key is None:
            raise ValueError("MEIP_OPENAI_API_KEY is required for the openai provider")
        self._key = settings.openai_api_key.get_secret_value()
        self._model = settings.openai_model
        self._url = settings.openai_base_url.rstrip("/") + "/responses"
        self._timeout = settings.openai_timeout_seconds
        self._attempts = settings.openai_max_attempts
        self._render_timeout = settings.pdf_check_timeout_seconds * 4
        self._client = client
        self._backoff = backoff
        self.model_version = f"{self._model}; {PROMPT_VERSION}"

    async def analyse(self, pdf: bytes) -> Layout:
        try:
            pages = await asyncio.to_thread(render_pdf, pdf, self._render_timeout)
        except RenderError as exc:
            raise ProviderError(str(exc)) from None
        lines: list[Line] = []
        raw_pages: list[dict[str, Any]] = []
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            for page in pages:
                cells = await self._transcribe(client, page)
                page_line_list, page_raw = page_lines(page, cells)
                lines.extend(page_line_list)
                raw_pages.append(
                    {
                        "number": page.number,
                        "width": page.width,
                        "height": page.height,
                        "text_layer": bool(page.segments),
                        "cells": page_raw,
                    }
                )
        finally:
            if self._client is None:
                await client.aclose()
        raw = {
            "source": "openai",
            "model": self._model,
            "prompt_version": PROMPT_VERSION,
            "unit": "pt",
            "origin": "top-left",
            "pages": raw_pages,
        }
        return Layout(
            unit="pt",
            pages={p.number: Page(p.number, p.width, p.height) for p in pages},
            lines=lines,
            raw=raw,
        )

    async def _transcribe(
        self, client: httpx.AsyncClient, page: RenderedPage
    ) -> list[dict[str, Any]]:
        image = base64.b64encode(page.png).decode("ascii")
        payload = {
            "model": self._model,
            "store": False,
            "temperature": 0,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": _hints(page)},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{image}"},
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "page_cells",
                    "schema": SCHEMA,
                    "strict": True,
                }
            },
        }
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        delay = self._backoff
        for attempt in range(1, self._attempts + 1):
            try:
                response = await client.post(self._url, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self._attempts:
                    raise ProviderUnavailableError("provider_unreachable") from None
            else:
                if response.status_code == 200:
                    break
                if response.status_code not in RETRY_STATUS:
                    # 400 (bad request), 401/403 (key or access), 404 (model): not retryable.
                    raise ProviderError(f"provider_http_{response.status_code}")
                if attempt == self._attempts:
                    raise ProviderUnavailableError(f"provider_http_{response.status_code}")
            await asyncio.sleep(delay)
            delay *= 2
        try:
            body = response.json()
            data = json.loads(_output_text(body))
            cells = data["cells"]
            if not isinstance(cells, list):
                raise TypeError
        except ProviderError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("malformed_response") from exc
        return [c for c in cells if isinstance(c, dict)]
