"""Paths and bounded resource limits for Protocol Miner."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    cas_root: Path
    max_artifact_size: int = 1024 * 1024 * 1024
    max_expanded_size: int = 2 * 1024 * 1024 * 1024
    max_file_count: int = 10_000
    max_recursion: int = 4

    @property
    def workspace_dir(self) -> Path:
        return self.root / "workspace"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def candidates_dir(self) -> Path:
        return self.root / "candidates"

    @property
    def quarantine_dir(self) -> Path:
        return self.root / "quarantine"

    def ensure_directories(self) -> None:
        for directory in (self.workspace_dir, self.reports_dir, self.candidates_dir, self.quarantine_dir):
            directory.mkdir(parents=True, exist_ok=True)


def default_settings(root: Path | None = None, cas_root: Path | None = None) -> Settings:
    app_root = (root or Path(__file__).resolve().parents[1]).resolve()
    shared_cas = (cas_root or app_root.parent / "artifacts").resolve()
    return Settings(root=app_root, cas_root=shared_cas)
