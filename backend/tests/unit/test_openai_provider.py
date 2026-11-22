"""Phase 4: the OpenAI layout provider, against a mocked API (no network, no real data).

The model only transcribes. These tests prove that, given the cells a model returns, the
provider builds the same layout the deterministic rules already read: text-layer cells take
the PDF's exact text and boxes; scanned pages keep the model's text and approximate boxes,
marked as such. Failures are retried or refused safely, and requests carry ``store: false``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from meip.core.config import Environment, ExtractionProvider, Settings
from meip.documents.extraction import (
    ProviderError,
    ProviderUnavailableError,
    SyntheticLayoutProvider,
    classify,
    extract,
)
from meip.documents.openai_provider import SCHEMA, OpenAILayoutProvider, page_lines
from meip.documents.pdfrender import RenderedPage, Segment, render_pdf
from meip.documents.statements import extract_statement
from tests.synthetic_docs import bank_statement_series, payslip_series, salaries_from


def settings(**kw: Any) -> Settings:
    base = {
        "env": Environment.TEST,
        "auth_mode": "dev",
        "extraction_provider": ExtractionProvider.OPENAI,
        "openai_api_key": "sk-test-not-a-real-key",
    }
    return Settings(**{**base, **kw})


def body(cells: list[dict[str, Any]], status: str = "completed") -> dict[str, Any]:
    return {
        "status": status,
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({"cells": cells})}],
            }
        ],
    }


def echo_segments(request: httpx.Request) -> httpx.Response:
    """A well-behaved model: one cell per text-layer segment offered in the prompt."""
    payload = json.loads(request.content)
    hint = payload["input"][1]["content"][0]["text"]
    rows = json.loads(hint.split("\n", 1)[1]) if "\n" in hint else []
    cells = [
        {
            "text": r["text"],
            "segment": r["index"],
            "box": dict(zip(("x0", "y0", "x1", "y1"), r["box"], strict=True)),
        }
        for r in rows
    ]
    return httpx.Response(200, json=body(cells))


def provider(handler: Any, **kw: Any) -> tuple[OpenAILayoutProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        result: httpx.Response = handler(request)
        return result

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return OpenAILayoutProvider(settings(**kw), client=client, backoff=0), seen


async def test_a_text_pdf_gives_exactly_the_layout_the_rules_expect() -> None:
    """Through the model, a payslip yields the same fields as its synthetic ground truth."""
    pdf = payslip_series(months=1)[0].document.pdf()
    p, seen = provider(echo_segments)
    layout = await p.analyse(pdf)
    truth = await SyntheticLayoutProvider().analyse(pdf)

    assert classify(layout.text).is_payslip
    got = [(f.field_code, f.raw_text, f.money_minor, f.value_date) for f in extract(layout).fields]
    want = [(f.field_code, f.raw_text, f.money_minor, f.value_date) for f in extract(truth).fields]
    assert got == want
    assert all(line.confidence == 1.0 for line in layout.lines)  # verified by the text layer
    assert {c["source"] for page in layout.raw["pages"] for c in page["cells"]} == {"text-layer"}
    assert layout.raw["source"] == "openai"

    # The request: structured outputs, nothing stored at OpenAI, the key only in the header.
    sent = json.loads(seen[0].content)
    assert sent["store"] is False
    assert sent["text"]["format"]["type"] == "json_schema"
    assert sent["text"]["format"]["strict"] is True
    assert sent["text"]["format"]["schema"] == SCHEMA
    assert seen[0].headers["authorization"] == "Bearer sk-test-not-a-real-key"
    assert "sk-test" not in json.dumps(layout.raw)
    assert "base64" not in json.dumps(layout.raw)  # no page images kept


async def test_a_statement_table_survives_transcription() -> None:
    [statement] = bank_statement_series(salaries_from(payslip_series(months=1)), months=1)
    pdf = statement.document.pdf()
    layout = await provider(echo_segments)[0].analyse(pdf)
    truth = await SyntheticLayoutProvider().analyse(pdf)
    got, want = extract_statement(layout), extract_statement(truth)
    assert got.refusal is None
    assert [(r.kind, r.paid_in, r.paid_out, r.balance) for r in got.rows] == [
        (r.kind, r.paid_in, r.paid_out, r.balance) for r in want.rows
    ]


def test_a_scanned_page_keeps_the_model_text_and_says_so() -> None:
    page = RenderedPage(number=1, width=600.0, height=800.0, png=b"", segments=[])
    lines, raw = page_lines(
        page,
        [
            {
                "text": "Net  Pay",
                "segment": None,
                "box": {"x0": 100, "y0": 500, "x1": 300, "y1": 520},
            },
            {
                "text": "2,805.84",
                "segment": None,
                "box": {"x0": 800, "y0": 500, "x1": 900, "y1": 520},
            },
            {
                "text": "upside down",
                "segment": None,
                "box": {"x0": 500, "y0": 500, "x1": 400, "y1": 520},
            },
            {"text": "far out", "segment": None, "box": {"x0": -50, "y0": 0, "x1": 2000, "y1": 10}},
        ],
    )
    assert [line.text for line in lines] == ["Net Pay", "2,805.84", "far out"]
    assert lines[0].box.x0 == pytest.approx(60.0)
    assert lines[0].box.y0 == pytest.approx(400.0)
    assert lines[2].box.x0 == 0.0
    assert lines[2].box.x1 == 600.0  # clamped to the page
    assert all(line.confidence is None for line in lines)
    assert {c["source"] for c in raw} == {"model"}


def test_a_text_layer_segment_wins_over_the_model_text() -> None:
    page = RenderedPage(1, 600.0, 800.0, b"", [Segment("Gross Pay", (50.0, 90.0, 110.0, 100.0))])
    lines, _ = page_lines(
        page, [{"text": "Gr0ss Pay", "segment": 0, "box": {"x0": 1, "y0": 1, "x1": 2, "y1": 2}}]
    )
    assert lines[0].text == "Gross Pay"
    assert (lines[0].box.x0, lines[0].box.y1) == (50.0, 100.0)


@pytest.mark.parametrize(
    ("responses", "error"),
    [
        ([httpx.Response(401, json={})], ProviderError),
        ([httpx.Response(400, json={})], ProviderError),
        ([httpx.Response(200, json={"output": []})], ProviderError),
        ([httpx.Response(200, json=body([], status="incomplete"))], ProviderError),
        ([httpx.Response(200, text="not json")], ProviderError),
        (
            [
                httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output": [{"type": "message", "content": [{"type": "refusal"}]}],
                    },
                )
            ],
            ProviderError,
        ),
        ([httpx.Response(503, json={})] * 3, ProviderUnavailableError),
        ([httpx.Response(429, json={})] * 3, ProviderUnavailableError),
    ],
)
async def test_failures_are_refused_or_retryable(
    responses: list[httpx.Response], error: type
) -> None:
    queue = list(responses)
    p, seen = provider(lambda _r: queue.pop(0))
    with pytest.raises(error):
        await p.analyse(payslip_series(months=1)[0].document.pdf())
    if error is ProviderUnavailableError:
        assert len(seen) == 3  # retried up to the configured attempts


async def test_a_rate_limit_then_success_is_retried() -> None:
    queue: list[Any] = [httpx.Response(429, json={}), None]

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        return item if item is not None else echo_segments(request)

    p, seen = provider(handler)
    layout = await p.analyse(payslip_series(months=1)[0].document.pdf())
    assert len(seen) == 2
    assert classify(layout.text).is_payslip


async def test_a_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderUnavailableError):
        await provider(handler)[0].analyse(payslip_series(months=1)[0].document.pdf())


async def test_an_unreadable_pdf_is_refused_without_calling_the_api() -> None:
    p, seen = provider(echo_segments)
    with pytest.raises(ProviderError):
        await p.analyse(b"%PDF-1.4 not really a pdf")
    assert seen == []


def test_the_renderer_cleans_up_its_temporary_files(tmp_path: Any, monkeypatch: Any) -> None:
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    render_pdf(payslip_series(months=1)[0].document.pdf(), 30)
    assert list(tmp_path.iterdir()) == []


def test_settings_require_a_key_and_https() -> None:
    from meip.core.config import ConfigurationError

    with pytest.raises(ConfigurationError):
        Settings(
            env=Environment.TEST,
            auth_mode="dev",
            documents_enabled=True,
            extraction_provider="openai",
        )
