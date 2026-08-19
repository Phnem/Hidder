"""Small, bounded reader for Electron ASAR archives (no Node/Electron execution)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from miner.unpack.safe_paths import safe_relative


def _header(raw: bytes) -> tuple[dict, int]:
    if len(raw) < 12 or struct.unpack_from("<I", raw, 0)[0] != 4:
        raise ValueError("invalid ASAR header-size pickle")
    pickle_size = struct.unpack_from("<I", raw, 4)[0]
    data_start = 8 + pickle_size
    if pickle_size < 4 or data_start > len(raw):
        raise ValueError("invalid ASAR header size")
    json_size = struct.unpack_from("<I", raw, 8)[0]
    if json_size > pickle_size - 4 or 12 + json_size > data_start:
        raise ValueError("invalid ASAR JSON size")
    try:
        return json.loads(raw[12:12 + json_size].decode("utf-8")), data_start
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid ASAR JSON header: {error}") from error


def _files(node: dict, prefix: Path = Path()) -> list[tuple[Path, dict]]:
    result: list[tuple[Path, dict]] = []
    for name, entry in node.get("files", {}).items():
        relative = safe_relative(str(prefix / name))
        if relative is None or not isinstance(entry, dict):
            raise ValueError(f"unsafe ASAR entry: {name}")
        if "files" in entry:
            result.extend(_files(entry, relative))
        else:
            result.append((relative, entry))
    return result


def extract(archive: Path, destination: Path, max_files: int, max_size: int) -> tuple[int, int]:
    raw = archive.read_bytes()
    header, data_start = _header(raw)
    files = _files(header)
    if len(files) > max_files:
        raise ValueError("ASAR file count exceeds limit")
    total = sum(int(entry.get("size", 0)) for _, entry in files if not entry.get("unpacked"))
    if total > max_size:
        raise ValueError("ASAR expanded size exceeds limit")
    for relative, entry in files:
        if entry.get("unpacked"):
            continue
        try:
            offset, size = int(entry["offset"]), int(entry["size"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid ASAR file metadata: {relative}") from error
        start, end = data_start + offset, data_start + offset + size
        if offset < 0 or size < 0 or end > len(raw):
            raise ValueError(f"ASAR file range escapes archive: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw[start:end])
    return len([entry for _, entry in files if not entry.get("unpacked")]), total
