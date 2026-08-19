"""Explicit cleanup of derived Protocol Miner data only."""

from __future__ import annotations

import shutil

from miner.config import Settings


def clean_workspace(settings: Settings) -> list[str]:
    """Remove only app-owned derived directories; shared CAS input bytes are retained."""
    root = settings.root.resolve()
    removed: list[str] = []
    for directory in (settings.workspace_dir, settings.reports_dir, settings.candidates_dir, settings.quarantine_dir):
        resolved = directory.resolve()
        if root not in resolved.parents:
            raise ValueError(f"refusing cleanup outside Protocol Miner root: {resolved}")
        if resolved.exists():
            shutil.rmtree(resolved)
            removed.append(str(resolved))
    return removed
