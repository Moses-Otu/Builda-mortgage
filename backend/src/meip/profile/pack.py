"""The evidence pack for one approval (Stage 3-C): a ZIP built only from the approval.

The generator reads **the approval row and the original files, nothing else**: never the
live profile, the firm's settings or current member names. So the same approval always gives
the same pack, byte for byte (fixed ZIP order and timestamps, ReportLab's invariant mode, no
current dates).

Every relied-on original is read in full and checked against the SHA-256 recorded in the
snapshot; one missing or altered original refuses the whole pack. Nothing is stored: the pack
is generated on request.

Contents: ``summary.pdf``, ``snapshot.json`` (the stored text, byte for byte),
``attestation.txt``, ``originals/<n>-<file name>`` and ``manifest.json``.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select

from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.service import get_case
from meip.core.errors import ConflictError, IntegrityFailureError, NotFoundError
from meip.documents.service import open_original
from meip.documents.storage import BlobStore
from meip.profile.models import ProfileApproval
from meip.profile.schemas import FigureOut, ProfileSnapshot
from meip.profile.service import sha256_of
from meip.tenancy.context import OperationContext

GENERATOR = "pack/1"


class OriginalUnavailableError(ConflictError):
    error_type = "original-unavailable"
    title = "An original document relied on by this approval is unavailable"


class PackSnapshotIntegrityError(ConflictError):
    error_type = "snapshot-integrity"
    title = "The approval's snapshot doesn't match its hash"


@dataclass(frozen=True)
class Pack:
    filename: str
    data: bytes
    sha256: str
    file_count: int
    approval_id: uuid.UUID
    version: int


# --- wording (from the snapshot only) -----------------------------------------------------------

_MEASURES = {
    "ytd_gross_pay": "Gross pay to date",
    "ytd_income_tax": "Income tax to date",
    "national_insurance": "National Insurance",
}
_ORIGINS = {"extracted": "E", "corrected": "C", "declared": "D", "broker_entry": "B"}


def _measure(code: str) -> str:
    return _MEASURES.get(code, code.replace("_", " ").capitalize())


def _money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    pounds, pence = divmod(abs(minor), 100)
    return f"{sign}£{pounds:,}.{pence:02d}"


def _day(d: date | datetime) -> str:
    return f"{d.day} {d:%b %Y}"


def _value(v: Any) -> str:
    data = v.model_dump() if hasattr(v, "model_dump") else dict(v)
    kind = data.get("type")
    if kind in ("money", "signed_money"):
        return _money(int(data["amount_minor"]))
    if kind in ("rate", "proportion"):
        return f"{data['percent']}%"
    if kind == "duration":
        return f"{data['months']} months"
    if kind == "count":
        return str(data["count"])
    if kind == "date" and data.get("date"):
        return _day(data["date"])
    return ""


def _period(f: FigureOut) -> str:
    if f.period_start and f.period_end:
        return f"{_day(f.period_start)} to {_day(f.period_end)}"
    if f.period_end:
        return f"period ending {_day(f.period_end)}"
    parts = [p for p in (f.frequency, f.tax_year and f"tax year {f.tax_year}") if p]
    if f.as_of_date:
        parts.append(f"as of {_day(f.as_of_date)}")
    return ", ".join(str(p).replace("_", "-") for p in parts)


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return cleaned[:80] or "document"


def _measure_inline(code: str) -> str:
    label = _measure(code)
    return label if code == "national_insurance" else label.lower()


def _documentary_line(d: Any) -> str:
    kind = "Payslip" if d.document_type == "payslip" else _measure(d.document_type)
    if not d.figures_saved:
        return f"{kind} ({d.file_name}), reviewed; no figures saved"
    span = (
        f" {_day(d.period_start)} to {_day(d.period_end)}"
        if d.period_start and d.period_end
        else ""
    )
    carried = ", ".join(_measure_inline(m) for m in d.measures)
    corrected = ", ".join(_measure_inline(m) for m in d.corrected_measures)
    return f"{kind} ({d.file_name}){span}: {carried}" + (
        f" ({corrected} corrected)" if corrected else ""
    )


def _holders(ids: list[uuid.UUID], joint: bool, people: dict[uuid.UUID, str]) -> str:
    names = " and ".join(people.get(h, "an applicant") for h in ids)
    return f"joint: {names}" if joint else f"held by {names}"


def _corroboration_line(c: Any, people: dict[uuid.UUID, str], files: dict[uuid.UUID, str]) -> str:
    when = _day(c.credit_date) if c.credit_date else "the date shown"
    statement = files.get(c.statement_document_id, "statement")
    return (
        f"Corroborating: credit of {_money(c.amount_minor or 0)} on {when} into account ending "
        f"{c.account_ending} ({_holders(c.holder_ids, c.joint, people)}), {statement} row "
        f"{c.row_number}. {c.scope}"
    )


def _finding_line(f: Any) -> str:
    ack = ""
    if f.acknowledgement is not None:
        note, when = f.acknowledgement.note, _day(f.acknowledgement.acknowledged_at)
        ack = f" Acknowledged: \u201c{note}\u201d ({when})."
    return f"[{f.status.replace('_', ' ')}] {f.title}. {f.message}{ack}"


def _text(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


# --- which originals the approval relied on ---------------------------------------------------


def relied_on(s: ProfileSnapshot) -> list[uuid.UUID]:
    """Documents that contributed a figure, or a statement that corroborates, in inventory
    order."""
    used = {f.evidence.document_id for f in s.figures if f.evidence is not None}
    for item in s.support:
        used |= {c.statement_document_id for c in item.corroborating}
    return [d.id for d in s.documents if d.id in used]


# --- the PDF ------------------------------------------------------------------------------------


class _NumberedCanvas(Canvas):  # type: ignore[misc]  # ReportLab has no type stubs
    """Adds "page n of m" and the case and version to every page's footer."""

    footer = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pages: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 (ReportLab's name)
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.grey)
            self.drawString(
                18 * mm,
                10 * mm,
                f"{self.footer} · page {self._pageNumber} of {total} · Not a lending decision",
            )
            super().showPage()
        super().save()


def _pdf(s: ProfileSnapshot, a: ProfileApproval, approver: str) -> bytes:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=8.5, leading=11)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5, textColor=colors.grey)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=15)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11.5, spaceBefore=8)
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=9.5, spaceBefore=6)

    def p(text: str, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(escape(text), style)

    people = {x.id: f"{x.first_name} {x.last_name}" for x in s.applicants}
    files = {d.id: d.original.original_filename or f"document {d.id}" for d in s.documents}
    reference = s.case.external_reference or f"Case {str(s.case.id)[:8]}"
    out: list[Any] = []

    # 1. Cover
    out += [
        p(f"Evidence pack: {reference}", h1),
        p("Synthetic data. Not a lending decision, an affordability assessment or advice."),
        Spacer(1, 4),
        p(f"Purpose: {s.case.purpose.replace('_', ' ')}"),
        p(
            "Applicants: "
            + "; ".join(
                f"{x.first_name} {x.last_name} ({x.party_role}"
                + (", removed)" if x.status == "removed" else ")")
                for x in s.applicants
            )
        ),
        p(
            f"Approval: version {a.version}, by {approver}, on {_day(a.approved_at)} "
            f"({a.approved_at:%H:%M} UTC)."
        ),
        p(f"Attested ({a.attestation_version}): {a.attestation_text}"),
        p(f"Profile rules: {a.profile_rules}. Snapshot SHA-256: {a.snapshot_sha256}", small),
    ]

    # 2. Items: figures with evidence, then what supports each
    def figure_rows(figs: list[FigureOut]) -> list[list[Any]]:
        rows: list[list[Any]] = [[p("Figure"), p("Period"), p("Value"), p("Origin"), p("Evidence")]]
        for f in figs:
            ev = ""
            if f.evidence is not None:
                e = f.evidence
                ev = (
                    f"{e.file_name or 'document'}, page {e.field.value_evidence.page_number}, "
                    f"'{e.field.label_text}'"
                )
                if f.origin == "corrected":
                    ev += f"; read as {e.field.raw_text}; reason: {e.correction_reason}"
            label = _measure(f.measure) + (
                f" ({f.qualifier.replace('_', ' ')})" if f.qualifier else ""
            )
            rows.append(
                [p(label), p(_period(f)), p(_value(f.value)), p(_ORIGINS[f.origin]), p(ev, small)]
            )
        return rows

    items: list[tuple[str, uuid.UUID]] = []
    for src in s.income_sources:
        items.append(
            (
                f"{src.name or src.kind.replace('_', ' ').capitalize() + ' income'} "
                f"({people.get(src.applicant_id, 'an applicant')})",
                src.id,
            )
        )
    for c in s.commitments:
        items.append((c.creditor or str(c.kind).replace("_", " ").capitalize(), c.id))
    for x in s.expenditure:
        items.append((x.description or str(x.category).replace("_", " ").capitalize(), x.id))

    out.append(p("Financial profile", h2))
    out.append(
        p(
            "Origin: E extracted (accepted as read), C corrected by the broker, D declared "
            "by the applicant, B broker entry. Nothing is chosen, added up, averaged or "
            "annualised.",
            small,
        )
    )
    for title, item_id in items:
        out.append(p(title, h3))
        figs = [f for f in s.figures if f.item_id == item_id]
        if figs:
            t = Table(
                figure_rows(figs),
                colWidths=[34 * mm, 38 * mm, 22 * mm, 12 * mm, 68 * mm],
                repeatRows=1,
            )
            t.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            out.append(t)
        else:
            out.append(p("No current figures."))
        support = next((x for x in s.support if x.item_id == item_id), None)
        if support is None:
            continue
        out.append(p("Supported by", h3))
        declared = [f for f in s.figures if f.id in support.declared]
        out.append(
            p(
                "Declared by the applicant (not verification): "
                + (
                    "; ".join(
                        f"{_measure(f.measure)} {_value(f.value)} {_period(f)}".strip()
                        for f in declared
                    )
                    or "None recorded"
                )
            )
        )
        docs = [_documentary_line(doc) for doc in support.documentary]
        out.append(
            p("Documentary evidence (broker-reviewed): " + ("; ".join(docs) or "None recorded"))
        )
        if support.corroborating:
            for cor in support.corroborating:
                out.append(p(_corroboration_line(cor, people, files)))
        else:
            out.append(p("Corroborating evidence: None recorded"))
        lines = [
            (
                "Discrepancy (acknowledged: " + dis.acknowledgement.note + ")"
                if dis.acknowledgement
                else "Discrepancy"
            )
            + f": {dis.title}"
            for dis in support.findings.discrepancies
        ]
        lines += [f"Could not run: {n.title} ({n.reason})" for n in support.findings.not_run]
        lines += [f"Passed: {x.title}" for x in support.findings.passed]
        out.append(p("Reconciliation findings: " + ("; ".join(lines) or "None recorded")))
        approved = f"version {a.version}, by {approver} on {_day(a.approved_at)}"
        out.append(p(f"Broker approval: approved, {approved}"))

    # 3. Checks
    out.append(p("Checks", h2))
    for version in s.checks.ruleset_versions:
        out.append(p(version, h3))
        for f in s.checks.findings:
            if f.ruleset_version != version:
                continue
            out.append(p(_finding_line(f)))

    # 4. Bank accounts
    if s.bank_accounts:
        out.append(p("Bank accounts", h2))
        for acc in s.bank_accounts:
            holders = _holders([h.applicant_id for h in acc.holders], acc.joint, people)
            stmts = ", ".join(files.get(sid, "statement") for sid in acc.statement_document_ids)
            label = f"{acc.label}, " if acc.label else ""
            ending = f"{label}account ending {acc.account_ending}"
            out.append(p(f"{ending}: {holders}. Statements: {stmts or 'none'}."))

    # 5. Inventory
    out.append(p("Documents", h2))
    used = set(relied_on(s))
    rows = [[p("File"), p("Type"), p("Status"), p("Filed under"), p("Relied on"), p("SHA-256")]]
    for doc in s.documents:
        status = _text(doc.status) + (f" ({doc.failure_code})" if doc.failure_code else "")
        rows.append(
            [
                p(doc.original.original_filename or "document"),
                p(_text(doc.document_type).replace("_", " ")),
                p(status),
                p(people.get(doc.applicant_id, "") if doc.applicant_id else ""),
                p("yes" if doc.id in used else "no"),
                p(doc.original.sha256[:16] + "\u2026", small),
            ]
        )
    t = Table(rows, colWidths=[46 * mm, 22 * mm, 26 * mm, 30 * mm, 16 * mm, 34 * mm], repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    out.append(t)

    buffer = io.BytesIO()
    canvas = type(
        "PackCanvas", (_NumberedCanvas,), {"footer": f"{reference} · version {a.version}"}
    )
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Evidence pack {reference} v{a.version}",
        author="MEIP",
        invariant=1,  # no creation dates or random ids: byte-identical output
    )
    doc.build(out, canvasmaker=canvas)
    return buffer.getvalue()


# --- the ZIP --------------------------------------------------------------------------------------


def _zip(files: list[tuple[str, bytes]], when: datetime) -> bytes:
    stamp = (max(when.year, 1980), when.month, when.day, when.hour, when.minute, when.second)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    return buffer.getvalue()


async def build(
    ctx: OperationContext, store: BlobStore, case_id: uuid.UUID, approval_id: uuid.UUID
) -> Pack:
    await get_case(ctx, case_id)  # 404 for an unknown case (or another firm's)
    a = await ctx.session.scalar(
        select(ProfileApproval).where(
            ProfileApproval.case_id == case_id, ProfileApproval.id == approval_id
        )
    )
    if a is None:
        raise NotFoundError("Approval not found")
    if sha256_of(a.snapshot) != a.snapshot_sha256:
        raise PackSnapshotIntegrityError()
    s = ProfileSnapshot.model_validate(json.loads(a.snapshot))
    by_id = {d.id: d for d in s.documents}

    # Every relied-on original, checked against the hash in the snapshot. One failure refuses
    # the whole pack.
    originals: list[tuple[str, bytes]] = []
    for n, doc_id in enumerate(relied_on(s), start=1):
        recorded = by_id[doc_id].original
        try:
            version_row, file = await open_original(ctx, store, case_id, doc_id)
        except (NotFoundError, IntegrityFailureError):
            raise OriginalUnavailableError(
                f"Original document {doc_id} is missing or doesn't match its recorded hash",
                extra={"document_id": str(doc_id)},
            ) from None
        try:
            data = file.read()
        finally:
            file.close()
        if (
            version_row.sha256 != recorded.sha256
            or hashlib.sha256(data).hexdigest() != recorded.sha256
        ):
            raise OriginalUnavailableError(
                f"Original document {doc_id} doesn't match the hash recorded in the approval",
                extra={"document_id": str(doc_id)},
            )
        name = _safe(recorded.original_filename or f"document-{doc_id}.pdf")
        originals.append((f"originals/{n:02d}-{name}", data))

    members = {m.user_id: m.name for m in s.members}
    approver = members.get(a.approved_by_user_id) or (
        f"firm member {str(a.approved_by_user_id)[:8]}" if a.approved_by_user_id else "unknown"
    )
    files: list[tuple[str, bytes]] = [
        ("summary.pdf", _pdf(s, a, approver)),
        ("snapshot.json", a.snapshot.encode("utf-8")),
        ("attestation.txt", f"{a.attestation_version}\n\n{a.attestation_text}\n".encode()),
        *originals,
    ]
    manifest = {
        "generator": GENERATOR,
        "approval": {
            "id": str(a.id),
            "case_id": str(a.case_id),
            "version": a.version,
            "approved_at": a.approved_at.isoformat(),
            "profile_rules": a.profile_rules,
            "snapshot_sha256": a.snapshot_sha256,
            "attestation_version": a.attestation_version,
        },
        "files": [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in files
        ],
    }
    files.append(
        ("manifest.json", (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    )
    data = _zip(files, a.approved_at)
    reference = _safe(s.case.external_reference or str(s.case.id)[:8])
    return Pack(
        filename=f"evidence-pack-{reference}-v{a.version}.zip",
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        file_count=len(files),
        approval_id=a.id,
        version=a.version,
    )


async def download(
    ctx: OperationContext, store: BlobStore, case_id: uuid.UUID, approval_id: uuid.UUID
) -> Pack:
    """Build the pack and record the download. A pack exports every relied-on original at
    once, so it is audited even though single downloads aren't yet (retention brief R17)."""
    pack = await build(ctx, store, case_id, approval_id)
    changes = audit.ChangeSet()
    for name, value in (
        ("approval_id", pack.approval_id),
        ("approval_version", pack.version),
        ("file_count", pack.file_count),
        ("pack_sha256", pack.sha256),
    ):
        changes.note(name, value)
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.EVIDENCE_PACK_DOWNLOADED,
        entity_type="profile_approval",
        entity_id=pack.approval_id,
        case_id=case_id,
        change_set=changes,
    )
    return pack
