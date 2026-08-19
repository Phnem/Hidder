"""File format detection based on magic bytes, content heuristics, and filename extensions."""

from pathlib import Path
from typing import Optional


class FileType:
    ZIP = "zip"
    SEVEN_ZIP = "7z"
    TAR = "tar"
    GZIP = "gz"
    PE_EXE = "pe_exe"
    MSI = "msi"
    JSON = "json"
    XML = "xml"
    INF = "inf"
    INI = "ini"
    JS = "js"
    SQLITE = "sqlite"
    TEXT = "text"
    UNKNOWN = "unknown"


def detect_file_type(file_path: Path, original_filename: Optional[str] = None) -> str:
    """Detect file format using magic bytes first, then extension and content heuristics."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return FileType.UNKNOWN

    try:
        with open(file_path, "rb") as f:
            header = f.read(512)
    except Exception:
        return FileType.UNKNOWN

    # 1. Check binary magic bytes
    if header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06") or header.startswith(b"PK\x07\x08"):
        return FileType.ZIP
    if header.startswith(b"7z\xbc\xaf\x27\x1c"):
        return FileType.SEVEN_ZIP
    if header.startswith(b"\x1f\x8b"):
        return FileType.GZIP
    if header.startswith(b"SQLite format 3\x00"):
        return FileType.SQLITE
    if header.startswith(b"MZ"):
        return FileType.PE_EXE
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return FileType.MSI

    # 2. Check original_filename extension if provided, else file_path.suffix
    ref_name = original_filename or file_path.name
    suffix = Path(ref_name).suffix.lower()
    if suffix in [".zip", ".whl", ".jar"]:
        return FileType.ZIP
    if suffix in [".7z"]:
        return FileType.SEVEN_ZIP
    if suffix in [".tar"]:
        return FileType.TAR
    if suffix in [".gz", ".tgz"]:
        return FileType.GZIP
    if suffix in [".json", ".json5"]:
        return FileType.JSON
    if suffix in [".xml", ".manifest", ".appxmanifest"]:
        return FileType.XML
    if suffix in [".inf"]:
        return FileType.INF
    if suffix in [".ini", ".cfg", ".toml", ".yaml", ".yml"]:
        return FileType.INI
    if suffix in [".js", ".mjs", ".cjs"]:
        return FileType.JS
    if suffix in [".sqlite", ".sqlite3", ".db"]:
        return FileType.SQLITE
    if suffix in [".txt", ".md", ".log"]:
        return FileType.TEXT
    if suffix in [".exe"]:
        return FileType.PE_EXE
    if suffix in [".msi"]:
        return FileType.MSI

    # 3. Content heuristics for CAS files without extension
    header_text = header.decode("latin-1", errors="ignore").strip()
    if header_text.startswith("{") or header_text.startswith("["):
        return FileType.JSON
    if header_text.startswith("<?xml") or header_text.startswith("<"):
        return FileType.XML
    if "[version]" in header_text.lower() or "[strings]" in header_text.lower():
        return FileType.INF
    if any(k in header_text for k in ["const ", "function", "var ", "let ", "export ", "import "]):
        return FileType.JS
    if any(k in header_text for k in ["=", "["]):
        return FileType.INI

    return FileType.UNKNOWN
