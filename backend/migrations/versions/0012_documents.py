"""Phase 2.0 step 2-A: uploaded documents and their immutable originals.

* ``documents``: a broker's upload within a case (optional applicant), type ``payslip``.
* ``document_versions``: the stored original - SHA-256, size, media type, generated storage
  key, optional (erasable) original file name. Immutable for every role: a trigger rejects
  UPDATE, DELETE and TRUNCATE.
* ``UNIQUE (tenant_id, case_id, sha256)``: identical content cannot be stored twice in one
  case. A unique index also holds under concurrent uploads (the second insert waits for the
  first and then fails).
* Runtime role: SELECT and INSERT only on both tables; no UPDATE, no DELETE.
* The downgrade refuses to run while any document exists: dropping the rows would silently
  orphan stored originals.

Self-contained: does not import application code.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
BLOB_KEY_PATTERN = rf"^{_UUID}/{_UUID}/{_UUID}/{_UUID}/original$"
TABLES = ("documents", "document_versions")


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=True),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_documents_tenant_id_case_id_id")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_documents_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["cases.tenant_id", "cases.id"],
            name=op.f("fk_documents_case"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "applicant_id"],
            ["applicants.tenant_id", "applicants.case_id", "applicants.id"],
            name=op.f("fk_documents_applicant"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_documents_created_by_user_id_users"),
        ),
        sa.CheckConstraint("document_type IN ('payslip')", name="document_type"),
        sa.CheckConstraint("status IN ('received')", name="status"),
    )
    for column in ("tenant_id", "case_id", "applicant_id"):
        op.create_index(op.f(f"ix_documents_{column}"), "documents", [column])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=False),
        sa.Column("blob_key", sa.String(200), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("scan_status", sa.String(16), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "sha256",
            name=op.f("uq_document_versions_tenant_id_case_id_sha256"),
        ),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name=op.f("uq_document_versions_document_id_version_number"),
        ),
        sa.UniqueConstraint("blob_key", name=op.f("uq_document_versions_blob_key")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_document_versions_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "document_id"],
            ["documents.tenant_id", "documents.case_id", "documents.id"],
            name=op.f("fk_document_versions_document"),
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name=op.f("fk_document_versions_uploaded_by_user_id_users"),
        ),
        sa.CheckConstraint("version_number = 1", name="version_number"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
        sa.CheckConstraint("size_bytes > 0", name="size_bytes"),
        sa.CheckConstraint("media_type = 'application/pdf'", name="media_type"),
        sa.CheckConstraint(f"blob_key ~ '{BLOB_KEY_PATTERN}'", name="blob_key"),
        sa.CheckConstraint("scan_status IN ('not_scanned')", name="scan_status"),
    )
    for column in ("tenant_id", "document_id"):
        op.create_index(op.f(f"ix_document_versions_{column}"), "document_versions", [column])

    # Originals are immutable for every role, the owner included.
    op.execute(
        """
        CREATE FUNCTION document_versions_immutable() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             RAISE EXCEPTION 'document versions are immutable (% refused)', TG_OP
               USING ERRCODE = 'integrity_constraint_violation';
           END $$
        """
    )
    op.execute(
        "CREATE TRIGGER document_versions_immutable BEFORE UPDATE OR DELETE "
        "ON document_versions FOR EACH ROW EXECUTE FUNCTION document_versions_immutable()"
    )
    op.execute(
        "CREATE TRIGGER document_versions_no_truncate BEFORE TRUNCATE "
        "ON document_versions FOR EACH STATEMENT EXECUTE FUNCTION document_versions_immutable()"
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant())"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM documents) THEN
            RAISE EXCEPTION 'Documents exist; downgrading 0012 would orphan their stored '
              'originals';
          END IF;
        END $$
        """
    )
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.execute("DROP FUNCTION IF EXISTS document_versions_immutable()")
