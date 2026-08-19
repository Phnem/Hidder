"""Shared path validation for archive readers."""

from pathlib import Path, PurePosixPath


def safe_relative(name: str) -> Path | None:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if not name or normalized.is_absolute() or ".." in normalized.parts:
        return None
    if normalized.parts and normalized.parts[0].endswith(":"):
        return None
    return Path(*normalized.parts)
