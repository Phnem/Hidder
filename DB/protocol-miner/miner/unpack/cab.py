"""Pure-Python, bounded CAB extraction; no Windows installer tools."""

from __future__ import annotations

from pathlib import Path

from cabarchive import CabArchive, CorruptionError, NotSupportedError

from miner.unpack.safe_paths import safe_relative


def extract(archive: Path, destination: Path, max_files: int, max_size: int) -> tuple[int, int]:
    cabinet = CabArchive(archive.read_bytes())
    entries = list(cabinet.items())
    if len(entries) > max_files:
        raise ValueError("CAB file count exceeds limit")
    total = sum(len(entry) for _, entry in entries)
    if total > max_size:
        raise ValueError("CAB expanded size exceeds limit")
    validated: list[tuple[Path, bytes]] = []
    for name, entry in entries:
        relative = safe_relative(name)
        if relative is None or entry.buf is None:
            raise ValueError(f"unsafe CAB entry: {name}")
        validated.append((relative, entry.buf))
    for relative, content in validated:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return len(validated), total
