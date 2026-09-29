"""Original uploaded files, kept for traceability and reprocessing. Local folder in development;
the same interface can sit on S3-compatible object storage in production."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from attendance_ai.core.errors import PermissionDeniedError


class FileStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def save(self, tenant_id: str, name: str, data: bytes) -> str:
        """Write atomically under the tenant's folder; returns the stored path relative to the root."""
        folder = self._tenant_root(tenant_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        os.replace(temporary, target)
        return f"{tenant_id}/{name}"

    def path_for(self, tenant_id: str, stored_path: str) -> Path:
        """Resolve a stored path, refusing anything outside the tenant's own folder."""
        path = (self._root / stored_path).resolve()
        if self._tenant_root(tenant_id) not in path.parents:
            raise PermissionDeniedError("The stored file is outside this tenant's storage.")
        return path

    def _tenant_root(self, tenant_id: str) -> Path:
        return (self._root / tenant_id).resolve()
