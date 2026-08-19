"""Safe archive extraction without calling installer executables or archive shell helpers."""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from miner.config import Settings
from miner.detect.file_type import detect
from miner.storage.cas import sha256_file

try:
    import py7zr
except ImportError:  # pragma: no cover - optional dependency is surfaced as capability state
    py7zr = None


@dataclass(frozen=True)
class UnpackResult:
    status: str
    output_dir: Path | None
    file_count: int
    total_bytes: int
    error: str | None = None


def _safe_relative(name: str) -> Path | None:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if not name or normalized.is_absolute() or ".." in normalized.parts:
        return None
    if normalized.parts and normalized.parts[0].endswith(":"):
        return None
    return Path(*normalized.parts)


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


class SafeUnpacker:
    """Extract only known archives with strict size, path, and link constraints."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def unpack(self, archive: Path, sha256: str) -> UnpackResult:
        destination = self.settings.workspace_dir / "unpacked" / sha256
        if destination.exists() and any(destination.iterdir()):
            files = [path for path in destination.rglob("*") if path.is_file()]
            return UnpackResult("already_unpacked", destination, len(files), sum(path.stat().st_size for path in files))
        kind = detect(archive)
        if kind == "zip":
            return self._zip(archive, destination)
        if kind == "7z":
            return self._seven_zip(archive, destination)
        if tarfile.is_tarfile(archive):
            return self._tar(archive, destination)
        return UnpackResult("not_archive", None, 0, 0, f"Static unpack unavailable for {kind}")

    def unpack_nested(self, root_result: UnpackResult, root_sha256: str) -> list[dict[str, object]]:
        """Recursively unpack nested containers within configured depth; never execute them."""
        if root_result.output_dir is None or root_result.status not in {"success", "already_unpacked"}:
            return []
        known = {root_sha256}
        queue: list[tuple[Path, str, int, str]] = [(path, root_sha256, 1, path.relative_to(root_result.output_dir).as_posix()) for path in root_result.output_dir.rglob("*") if path.is_file()]
        children: list[dict[str, object]] = []
        while queue:
            path, parent_sha256, depth, relative_path = queue.pop(0)
            kind = detect(path, path.name)
            if kind not in {"zip", "7z"} and not tarfile.is_tarfile(path):
                continue
            child_sha256 = sha256_file(path)
            if child_sha256 in known:
                continue
            known.add(child_sha256)
            if depth > self.settings.max_recursion:
                children.append({"sha256": child_sha256, "parent_artifact": parent_sha256, "relative_path": relative_path, "detected_type": kind, "status": "recursion_limit"})
                continue
            result = self.unpack(path, child_sha256)
            child = {"sha256": child_sha256, "parent_artifact": parent_sha256, "relative_path": relative_path, "detected_type": kind, "status": result.status, "file_count": result.file_count, "error": result.error}
            children.append(child)
            if result.output_dir is not None and result.status in {"success", "already_unpacked"}:
                queue.extend((nested, child_sha256, depth + 1, nested.relative_to(result.output_dir).as_posix()) for nested in result.output_dir.rglob("*") if nested.is_file())
        return children

    def _seven_zip(self, archive: Path, destination: Path) -> UnpackResult:
        if py7zr is None:
            return UnpackResult("unavailable", None, 0, 0, "py7zr is not installed")
        try:
            with py7zr.SevenZipFile(archive, mode="r") as bundle:
                entries = bundle.list()
                if len(entries) > self.settings.max_file_count:
                    return UnpackResult("safety_violation", None, 0, 0, "archive file count exceeds limit")
                total = sum(entry.uncompressed for entry in entries if entry.is_file)
                if total > self.settings.max_expanded_size:
                    return UnpackResult("safety_violation", None, 0, 0, "expanded size exceeds limit")
                for entry in entries:
                    if _safe_relative(entry.filename) is None or entry.is_symlink or not (entry.is_file or entry.is_directory):
                        return UnpackResult("safety_violation", None, 0, 0, f"unsafe archive entry: {entry.filename}")
                self._prepare(destination)
                bundle.extract(path=destination)
            files = [path for path in destination.rglob("*") if path.is_file()]
            if any(path.is_symlink() for path in destination.rglob("*")):
                return UnpackResult("safety_violation", None, 0, 0, "symlink was produced by archive")
            return UnpackResult("success", destination, len(files), sum(path.stat().st_size for path in files))
        except (OSError, py7zr.Bad7zFile) as error:
            return UnpackResult("error", None, 0, 0, str(error))

    def _prepare(self, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)

    def _zip(self, archive: Path, destination: Path) -> UnpackResult:
        try:
            with zipfile.ZipFile(archive) as bundle:
                entries = bundle.infolist()
                if len(entries) > self.settings.max_file_count:
                    return UnpackResult("safety_violation", None, 0, 0, "archive file count exceeds limit")
                total = sum(entry.file_size for entry in entries)
                compressed = max(archive.stat().st_size, 1)
                if total > self.settings.max_expanded_size:
                    return UnpackResult("safety_violation", None, 0, 0, "expanded size exceeds limit")
                if total / compressed > 100:
                    return UnpackResult("safety_violation", None, 0, 0, "compression ratio exceeds limit")
                validated: list[tuple[zipfile.ZipInfo, Path]] = []
                for entry in entries:
                    relative = _safe_relative(entry.filename)
                    if relative is None or _is_zip_symlink(entry):
                        return UnpackResult("safety_violation", None, 0, 0, f"unsafe archive entry: {entry.filename}")
                    validated.append((entry, relative))
                self._prepare(destination)
                count = written = 0
                for entry, relative in validated:
                    target = destination / relative
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as source, target.open("wb") as sink:
                        written += shutil.copyfileobj(source, sink, length=1024 * 1024) or entry.file_size
                    count += 1
                return UnpackResult("success", destination, count, total)
        except (OSError, zipfile.BadZipFile) as error:
            return UnpackResult("error", None, 0, 0, str(error))

    def _tar(self, archive: Path, destination: Path) -> UnpackResult:
        try:
            with tarfile.open(archive, "r:*") as bundle:
                entries = bundle.getmembers()
                if len(entries) > self.settings.max_file_count:
                    return UnpackResult("safety_violation", None, 0, 0, "archive file count exceeds limit")
                total = sum(entry.size for entry in entries if entry.isfile())
                if total > self.settings.max_expanded_size:
                    return UnpackResult("safety_violation", None, 0, 0, "expanded size exceeds limit")
                validated: list[tuple[tarfile.TarInfo, Path]] = []
                for entry in entries:
                    relative = _safe_relative(entry.name)
                    if relative is None or entry.issym() or entry.islnk() or entry.isdev() or entry.isfifo():
                        return UnpackResult("safety_violation", None, 0, 0, f"unsafe archive entry: {entry.name}")
                    validated.append((entry, relative))
                self._prepare(destination)
                count = 0
                for entry, relative in validated:
                    target = destination / relative
                    if entry.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif entry.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = bundle.extractfile(entry)
                        if source is None:
                            return UnpackResult("error", None, 0, 0, f"unable to read {entry.name}")
                        with source, target.open("wb") as sink:
                            shutil.copyfileobj(source, sink, length=1024 * 1024)
                        count += 1
                return UnpackResult("success", destination, count, total)
        except (OSError, tarfile.TarError) as error:
            return UnpackResult("error", None, 0, 0, str(error))
