"""Operator-only deletion of stored originals (Phase 4 retention erasure).

Kept out of :mod:`meip.documents.storage` on purpose: the application's storage layer has no
delete operation at all (a Phase 2 safeguard, enforced by a test). Only the erasure CLI,
running as the operator after the database rows are gone, reaches this module.
"""

from __future__ import annotations

import asyncio
import contextlib

from meip.documents.storage import AzureBlobStore, BlobKey, FilesystemBlobStore


def _erase_file(store: FilesystemBlobStore, key: BlobKey) -> bool:
    path = store._path(key)  # validated: stays inside the store's root
    path.unlink(missing_ok=True)
    return not path.exists()


def _erase_azure(store: AzureBlobStore, key: BlobKey) -> bool:
    from azure.core.exceptions import ResourceNotFoundError

    with contextlib.suppress(ResourceNotFoundError):
        store._container.delete_blob(key.value)
    return True


async def erase_blob(store: object, key: str) -> bool:
    """Delete one stored original. Returns whether it is now gone."""
    if isinstance(store, FilesystemBlobStore):
        return await asyncio.to_thread(_erase_file, store, BlobKey(key))
    if isinstance(store, AzureBlobStore):
        return await asyncio.to_thread(_erase_azure, store, BlobKey(key))
    raise TypeError("unknown blob store")
