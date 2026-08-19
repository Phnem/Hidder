"""Compatible SHA-256 CAS storage; source files are copied or hard-linked, never modified."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ContentAddressedStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def put_file(self, source: Path) -> tuple[str, Path, bool]:
        sha256 = sha256_file(source)
        destination = self.path_for(sha256)
        if destination.exists():
            return sha256, destination, False
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)
        return sha256, destination, True
