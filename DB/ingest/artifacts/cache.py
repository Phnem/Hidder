"""Content-Addressed Storage (CAS) for downloaded artifacts."""

import hashlib
import shutil
from pathlib import Path
from typing import Optional, Union

from ingest.config import ARTIFACTS_DIR
from ingest.logging_setup import log_artifact, get_logger


def compute_sha256_of_file(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Calculate SHA-256 digest of a local file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def compute_sha256_of_bytes(data: bytes) -> str:
    """Calculate SHA-256 digest of bytes in memory."""
    return hashlib.sha256(data).hexdigest()


class ArtifactCache:
    def __init__(self, base_dir: Path = ARTIFACTS_DIR):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_cas_path(self, sha256: str) -> Path:
        """Returns artifacts/ab/abcdef... path for a given SHA256."""
        prefix = sha256[:2].lower()
        folder = self.base_dir / prefix
        folder.mkdir(parents=True, exist_ok=True)
        return folder / sha256.lower()

    def get_artifact_path(self, sha256: str) -> Path:
        return self.get_cas_path(sha256)

    def has_artifact(self, sha256: str) -> bool:
        """Check if artifact is already stored in CAS cache."""
        path = self.get_cas_path(sha256)
        return path.exists() and path.stat().st_size > 0

    def store_file(self, temp_path: Path, expected_sha256: Optional[str] = None) -> tuple[str, Path]:
        """
        Move or copy a temporary file into CAS storage.
        Returns (sha256, cas_path).
        """
        actual_sha = compute_sha256_of_file(temp_path)
        if expected_sha256 and actual_sha.lower() != expected_sha256.lower():
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual_sha}")

        target_path = self.get_cas_path(actual_sha)
        if not target_path.exists():
            shutil.copy2(temp_path, target_path)
            log_artifact(f"Saved into CAS: {actual_sha[:12]}... -> {target_path}")
        else:
            log_artifact(f"Cache HIT in CAS for {actual_sha[:12]}...")

        return actual_sha, target_path
