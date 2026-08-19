"""Safe static extraction of archives with strict security limits."""

import os
import tarfile
import zipfile
from pathlib import Path
from typing import Optional, NamedTuple

from ingest.artifacts.file_types import detect_file_type, FileType
from ingest.config import EXTRACTED_DIR, settings
from ingest.logging_setup import log_extract, get_logger

logger = get_logger()

# Optional py7zr support
try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False


class ExtractionResult(NamedTuple):
    status: str  # "success", "unsupported", "safety_violation", "error"
    extracted_path: Optional[Path]
    file_count: int
    total_uncompressed_bytes: int
    error_message: Optional[str] = None


def is_safe_path(base_dir: Path, target_path: Path) -> bool:
    """Ensure target_path does not escape base_dir via symlinks or path traversal."""
    try:
        resolved_base = base_dir.resolve()
        resolved_target = target_path.resolve()
        return resolved_base in resolved_target.parents or resolved_base == resolved_target
    except Exception:
        return False


class SafeExtractor:
    def __init__(self, output_base: Path = EXTRACTED_DIR):
        self.output_base = output_base
        self.output_base.mkdir(parents=True, exist_ok=True)

    def extract(self, archive_path: Path, sha256: str) -> ExtractionResult:
        """
        Safely unpack an archive into a sandbox directory.
        Zero executable execution.
        """
        target_dir = self.output_base / sha256.lower()
        if target_dir.exists() and any(target_dir.iterdir()):
            log_extract(f"Already extracted in {target_dir}")
            files = list(target_dir.rglob("*"))
            total_size = sum(f.stat().st_size for f in files if f.is_file())
            return ExtractionResult("success", target_dir, len(files), total_size)

        target_dir.mkdir(parents=True, exist_ok=True)
        file_type = detect_file_type(archive_path)

        log_extract(f"Analyzing archive: {archive_path.name} (Detected format: {file_type})")

        # 1. ZIP or SFX-ZIP
        if file_type == FileType.ZIP or self._is_zip_or_sfx(archive_path):
            return self._extract_zip(archive_path, target_dir)

        # 2. 7z
        if file_type == FileType.SEVEN_ZIP:
            if HAS_PY7ZR:
                return self._extract_7z(archive_path, target_dir)
            else:
                logger.warning(f"[extract] 7z file detected but py7zr is not installed: {archive_path.name}")
                return ExtractionResult("unsupported", None, 0, 0, "py7zr library not installed")

        # 3. TAR / GZ
        if file_type in [FileType.TAR, FileType.GZIP]:
            return self._extract_tar(archive_path, target_dir)

        # Non-archive or unsupported installer
        log_extract(f"File is not a supported extractable archive format ({file_type}). Static file inspection only.")
        return ExtractionResult("unsupported", None, 0, 0, f"Unsupported container format: {file_type}")

    def _is_zip_or_sfx(self, path: Path) -> bool:
        """Check if file is a ZIP or Self-Extracting ZIP executable."""
        try:
            return zipfile.is_zipfile(path)
        except Exception:
            return False

    def _extract_zip(self, archive_path: Path, target_dir: Path) -> ExtractionResult:
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                infolist = zf.infolist()
                
                # Check limits
                if len(infolist) > settings.max_extracted_files:
                    return ExtractionResult("safety_violation", None, 0, 0, f"File count ({len(infolist)}) exceeds limit")

                total_uncompressed = sum(info.file_size for info in infolist)
                compressed_size = archive_path.stat().st_size or 1
                ratio = total_uncompressed / compressed_size

                if total_uncompressed > settings.max_extracted_size_bytes:
                    return ExtractionResult("safety_violation", None, 0, 0, f"Uncompressed size exceeds limit ({total_uncompressed} bytes)")
                if ratio > settings.max_compression_ratio:
                    return ExtractionResult("safety_violation", None, 0, 0, f"Zip bomb suspect: compression ratio {ratio:.1f}x exceeds limit")

                extracted_count = 0
                for info in infolist:
                    filename = info.filename
                    # Reject traversal patterns
                    if filename.startswith("/") or filename.startswith("\\") or ".." in filename or (len(filename) > 1 and filename[1] == ":"):
                        logger.warning(f"[extract] Skipping suspicious path in zip: {filename}")
                        continue

                    dest_file = target_dir / filename
                    if not is_safe_path(target_dir, dest_file):
                        logger.warning(f"[extract] Path traversal blocked: {filename}")
                        continue

                    if info.is_dir():
                        dest_file.mkdir(parents=True, exist_ok=True)
                    else:
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as source, open(dest_file, "wb") as target:
                            target.write(source.read())
                        extracted_count += 1

                log_extract(f"Successfully extracted {extracted_count} files ({total_uncompressed / 1024 / 1024:.2f} MB)")
                return ExtractionResult("success", target_dir, extracted_count, total_uncompressed)
        except Exception as e:
            logger.error(f"[extract] ZIP extraction error for {archive_path.name}: {e}", exc_info=True)
            return ExtractionResult("error", None, 0, 0, str(e))

    def _extract_7z(self, archive_path: Path, target_dir: Path) -> ExtractionResult:
        try:
            with py7zr.SevenZipFile(archive_path, mode="r") as sz:
                files_map = sz.getnames()
                if len(files_map) > settings.max_extracted_files:
                    return ExtractionResult("safety_violation", None, 0, 0, "Too many files in 7z")

                sz.extractall(path=target_dir)
                all_files = list(target_dir.rglob("*"))
                count = len([f for f in all_files if f.is_file()])
                total_sz = sum(f.stat().st_size for f in all_files if f.is_file())
                log_extract(f"Successfully unpacked 7z: {count} files ({total_sz / 1024 / 1024:.2f} MB)")
                return ExtractionResult("success", target_dir, count, total_sz)
        except Exception as e:
            logger.error(f"[extract] 7z extraction error: {e}", exc_info=True)
            return ExtractionResult("error", None, 0, 0, str(e))

    def _extract_tar(self, archive_path: Path, target_dir: Path) -> ExtractionResult:
        try:
            with tarfile.open(archive_path, "r:*") as tf:
                members = tf.getmembers()
                if len(members) > settings.max_extracted_files:
                    return ExtractionResult("safety_violation", None, 0, 0, "Too many files in tar")

                total_uncompressed = sum(m.size for m in members)
                if total_uncompressed > settings.max_extracted_size_bytes:
                    return ExtractionResult("safety_violation", None, 0, 0, "Tar size exceeds limit")

                extracted_count = 0
                for m in members:
                    if not m.name or ".." in m.name or m.name.startswith("/") or m.name.startswith("\\"):
                        continue
                    dest = target_dir / m.name
                    if not is_safe_path(target_dir, dest):
                        continue
                    tf.extract(m, path=target_dir)
                    if m.isfile():
                        extracted_count += 1

                log_extract(f"Successfully extracted tar: {extracted_count} files")
                return ExtractionResult("success", target_dir, extracted_count, total_uncompressed)
        except Exception as e:
            logger.error(f"[extract] Tar extraction error: {e}", exc_info=True)
            return ExtractionResult("error", None, 0, 0, str(e))
