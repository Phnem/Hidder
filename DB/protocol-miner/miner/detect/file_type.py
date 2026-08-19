"""Conservative type detector: magic bytes first, suffix only as a secondary signal."""

from pathlib import Path


def detect(path: Path, original_filename: str | None = None) -> str:
    with path.open("rb") as handle:
        head = handle.read(1024)
    if head.startswith((b"PK\\x03\\x04", b"PK\\x05\\x06", b"PK\\x07\\x08")):
        return "zip"
    if head.startswith(b"7z\\xbc\\xaf\\x27\\x1c"):
        return "7z"
    if head.startswith(b"MZ"):
        return "pe"
    if head.startswith(b"\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1"):
        return "msi_or_ole"
    if head.startswith(b"SQLite format 3\\x00"):
        return "sqlite"
    if head.startswith(b":") and (path.suffix.lower() == ".hex"):
        return "intel_hex"
    suffix = Path(original_filename or path.name).suffix.lower()
    suffix_types = {
        ".asar": "asar", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".json": "json", ".json5": "json", ".inf": "inf", ".uf2": "uf2", ".bin": "firmware_binary",
        ".exe": "pe", ".dll": "pe", ".zip": "zip", ".7z": "7z", ".msi": "msi_or_ole",
    }
    return suffix_types.get(suffix, "unknown")
