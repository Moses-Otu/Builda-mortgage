"""Step 2-A unit tests: blob storage, fail-closed settings, upload helpers."""

from __future__ import annotations

import ast
import io
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from meip.core.config import AuthMode, BlobBackend, ConfigurationError, Environment, Settings
from meip.core.errors import BusinessRuleError, PayloadTooLargeError, UnsupportedMediaTypeError
from meip.documents import service
from meip.documents.storage import (
    AzureBlobStore,
    BlobExistsError,
    BlobKey,
    BlobNotFoundError,
    FilesystemBlobStore,
    orphaned_keys,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "meip"


def _key() -> BlobKey:
    return BlobKey.original(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


# --- keys -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "../../etc/passwd",
        f"{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/original/../x",
        f"{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/original",
        str(uuid.uuid4()).upper() + f"/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/original",
        f"{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4()}/payslip.pdf",
    ],
)
def test_only_generated_keys_are_valid(value: str) -> None:
    with pytest.raises(ValueError, match="blob key"):
        BlobKey(value)


# --- filesystem store -------------------------------------------------------------------


async def test_filesystem_store_is_create_only(tmp_dir: Path) -> None:
    store = FilesystemBlobStore(tmp_dir)
    key = _key()
    await store.put_new(key, io.BytesIO(b"first"))
    with pytest.raises(BlobExistsError):
        await store.put_new(key, io.BytesIO(b"second"))
    sink = io.BytesIO()
    await store.read_into(key, sink)
    assert sink.getvalue() == b"first"
    assert await store.list_keys() == [key.value]
    leftovers = [p for p in tmp_dir.rglob("*") if p.is_file() and p.name != "original"]  # noqa: ASYNC240
    assert leftovers == []  # no temporary files left behind


async def test_filesystem_store_missing_key(tmp_dir: Path) -> None:
    with pytest.raises(BlobNotFoundError):
        await FilesystemBlobStore(tmp_dir).read_into(_key(), io.BytesIO())
    assert await FilesystemBlobStore(tmp_dir / "absent").list_keys() == []


def test_orphaned_keys() -> None:
    assert orphaned_keys(["a", "b", "c"], {"b"}) == ["a", "c"]


def test_stores_cannot_delete() -> None:
    """No delete operation exists in the storage layer, and it never calls one."""
    for store in (FilesystemBlobStore, AzureBlobStore):
        names = {n for n in dir(store) if not n.startswith("__")}
        assert not {n for n in names if "delete" in n or "remove" in n}
    tree = ast.parse((SRC / "documents" / "storage.py").read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & {"delete_blob", "delete_blobs", "delete_container", "rmtree", "remove"}
    # The only unlink is of the temporary name, never the stored target.
    assert (
        sum(1 for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "unlink") == 1
    )


# --- Azure store against Azurite (opt-in) ------------------------------------------------


@pytest.mark.azurite
async def test_azure_store_against_azurite() -> None:
    """Runs only with ``-m azurite`` and MEIP_TEST_AZURITE_CONNECTION_STRING set. Verifies the
    adapter against the local emulator, not against a real Azure storage account."""
    connection = os.environ.get("MEIP_TEST_AZURITE_CONNECTION_STRING")
    if not connection:
        pytest.skip("MEIP_TEST_AZURITE_CONNECTION_STRING not set")
    store = AzureBlobStore(connection, f"test-{uuid.uuid4().hex[:12]}")
    assert await store.ensure_container() is True
    assert await store.ensure_container() is False
    assert await store.public_access() is None  # private: no anonymous access
    key = _key()
    await store.put_new(key, io.BytesIO(b"azurite"))
    with pytest.raises(BlobExistsError):
        await store.put_new(key, io.BytesIO(b"overwrite"))
    sink = io.BytesIO()
    await store.read_into(key, sink)
    assert sink.getvalue() == b"azurite"
    assert await store.list_keys() == [key.value]
    with pytest.raises(BlobNotFoundError):
        await store.read_into(_key(), io.BytesIO())


# --- fail-closed settings (P2-8) ---------------------------------------------------------

_OIDC = {
    "auth_mode": AuthMode.OIDC,
    "oidc_issuer": "https://issuer.example/",
    "oidc_audience": "api",
    "oidc_jwks_url": "https://issuer.example/keys",
}


def _make(**values: object) -> Settings:
    """Settings from explicit values only (no environment variables or .env file)."""
    return Settings.model_validate({**_OIDC, **values})


_HTTPS = "DefaultEndpointsProtocol=https;AccountName=meip;AccountKey=a2V5"


def test_documents_are_off_by_default() -> None:
    assert _make(env=Environment.PRODUCTION).documents_enabled is False


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PRODUCTION])
def test_deployed_documents_need_the_retention_approval(env: Environment) -> None:
    with pytest.raises(ConfigurationError, match="RETENTION_APPROVAL"):
        _make(env=env, documents_enabled=True, blob_connection_string=_HTTPS)
    with pytest.raises(ConfigurationError, match="RETENTION_APPROVAL"):
        _make(
            env=env,
            documents_enabled=True,
            blob_connection_string=_HTTPS,
            document_retention_approval="  ",
        )
    approved = _make(
        env=env,
        documents_enabled=True,
        blob_connection_string=_HTTPS,
        document_retention_approval="D10-approved",
    )
    assert approved.documents_enabled


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PRODUCTION])
def test_filesystem_storage_is_never_allowed_when_deployed(env: Environment) -> None:
    with pytest.raises(ConfigurationError, match="filesystem"):
        _make(env=env, blob_backend=BlobBackend.FILESYSTEM)


_AZURITE = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=a2V5;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


@pytest.mark.parametrize(
    ("connection", "message"),
    [
        (_AZURITE, "HTTPS"),
        ("DefaultEndpointsProtocol=https;AccountName=devstoreaccount1;AccountKey=a2V5", "Azurite"),
        ("UseDevelopmentStorage=true", "HTTPS"),
        (
            "DefaultEndpointsProtocol=https;AccountName=prod;AccountKey=a2V5;"
            "BlobEndpoint=http://prod.example/",
            "HTTPS",
        ),
    ],
)
def test_deployed_storage_must_be_https_and_not_azurite(connection: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message) as raised:
        _make(
            env=Environment.PRODUCTION,
            documents_enabled=True,
            document_retention_approval="D10-approved",
            blob_connection_string=connection,
        )
    assert "a2V5" not in str(raised.value)  # the account key is never echoed


def test_deployed_https_account_is_accepted() -> None:
    settings = _make(
        env=Environment.PRODUCTION,
        documents_enabled=True,
        document_retention_approval="D10-approved",
        blob_connection_string=(
            "DefaultEndpointsProtocol=https;AccountName=meipprod;AccountKey=a2V5;"
            "EndpointSuffix=core.windows.net"
        ),
    )
    assert settings.documents_enabled
    assert _make(env=Environment.LOCAL, auth_mode=AuthMode.DEV, blob_connection_string=_AZURITE)


def test_only_the_uniqueness_index_counts_as_a_duplicate() -> None:
    from sqlalchemy.exc import IntegrityError

    class _DriverError(Exception):
        def __init__(self, sqlstate: str, constraint_name: str) -> None:
            self.sqlstate, self.constraint_name = sqlstate, constraint_name

    def violation(sqlstate: str, name: str) -> IntegrityError:
        return IntegrityError("INSERT", {}, _DriverError(sqlstate, name))

    assert service._is_duplicate_violation(violation("23505", service.DUPLICATE_CONSTRAINT))
    assert not service._is_duplicate_violation(violation("23514", "ck_document_versions_sha256"))
    assert not service._is_duplicate_violation(violation("23505", "uq_document_versions_blob_key"))


def test_azure_backend_needs_a_connection_string() -> None:
    with pytest.raises(ConfigurationError, match="CONNECTION_STRING"):
        _make(env=Environment.LOCAL, auth_mode=AuthMode.DEV, documents_enabled=True)


# --- upload helpers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("payslip.pdf", "payslip.pdf"),
        ("..%2F..%2Fsecret.pdf", "secret.pdf"),
        ("C:%5Cfolder%5Cpay.pdf", "pay.pdf"),
        ("a%0D%0Ab.pdf", "ab.pdf"),
        ("%E2%80%AEfdp.exe", "fdp.exe"),  # right-to-left override removed
        ("%20%20", None),
        ("x" * 300, "x" * 255),
        ("%FF%FE.pdf", "��.pdf"),
    ],
)
def test_clean_filename(raw: str | None, expected: str | None) -> None:
    assert service.clean_filename(raw) == expected


@pytest.mark.parametrize(
    ("content_type", "length", "error"),
    [
        ("application/pdf", "10", None),
        ("Application/PDF; charset=binary", None, None),
        ("application/octet-stream", "10", UnsupportedMediaTypeError),
        (None, "10", UnsupportedMediaTypeError),
        ("application/pdf", "101", PayloadTooLargeError),
        ("application/pdf", "nonsense", None),  # left to the streaming limit
    ],
)
def test_check_declared(
    content_type: str | None, length: str | None, error: type[Exception] | None
) -> None:
    if error is None:
        service.check_declared(content_type, length, 100)
    else:
        with pytest.raises(error):
            service.check_declared(content_type, length, 100)


async def _stream(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_receive_pdf_hashes_while_streaming() -> None:
    received = await service.receive_pdf(_stream(b"%PDF-", b"1.4 body"), max_bytes=100)
    try:
        assert received.size == 13
        assert received.file.read() == b"%PDF-1.4 body"
    finally:
        received.close()


@pytest.mark.parametrize(
    ("parts", "error"),
    [
        ((b"%PDF-", b"x" * 96), PayloadTooLargeError),
        ((), BusinessRuleError),
        ((b"%PD",), UnsupportedMediaTypeError),
    ],
)
async def test_receive_pdf_rejects(parts: tuple[bytes, ...], error: type[Exception]) -> None:
    with pytest.raises(error):
        await service.receive_pdf(_stream(*parts), max_bytes=100)
