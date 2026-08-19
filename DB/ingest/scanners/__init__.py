"""Unified static scanner dispatcher."""

from pathlib import Path
from typing import NamedTuple, Optional

from ingest.artifacts.file_types import detect_file_type, FileType
from ingest.normalize.evidence import DeviceIdentifierFact, ProtocolHintFact, GenericFact
from ingest.scanners.binary_strings import BinaryStringsScanner
from ingest.scanners.inf_scanner import InfScanner
from ingest.scanners.js_scanner import JsScanner
from ingest.scanners.json_scanner import JsonScanner, ParsedDeviceRecord
from ingest.scanners.sqlite_scanner import SqliteScanner
from ingest.scanners.text_scanner import TextScanner
from ingest.scanners.xml_scanner import XmlScanner
from ingest.logging_setup import get_logger

logger = get_logger()


class AggregatedScanResult(NamedTuple):
    identifiers: list[DeviceIdentifierFact]
    hints: list[ProtocolHintFact]
    facts: list[GenericFact]
    device_records: list[ParsedDeviceRecord] = []


ALWAYS_SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".cur", ".ani",
    ".wav", ".mp3", ".ogg", ".flac", ".aac", ".wma",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".css", ".scss", ".less", ".html", ".htm",
    ".res", ".rc", ".resources", ".pdb", ".chm", ".pdf", ".rtf", ".doc", ".docx"
}

MARKUP_UI_EXTENSIONS = {".xaml", ".baml", ".qml", ".axaml", ".cml"}

TECH_CONTEXT_TERMS = {
    "device", "config", "protocol", "firmware", "driver", "hw", "hardware",
    "profile", "vid", "pid", "setting", "matrix", "endpoint"
}

UI_RESOURCE_TERMS = {
    "ui", "view", "theme", "skin", "resource", "style", "icon", "dialog", "window",
    "button", "slider", "menu", "textbox", "textblock", "shape", "dict_", "locale",
    "lang", "i18n", "animation", "visual"
}


THIRD_PARTY_BUNDLE_TERMS = {
    "vcredist", "vc_redist", "microsoft vc++", "mfc1", "msvcp", "msvcr", "ucrtbase",
    "vcruntime", "iot_driver", "dotnetfx", "dotnet", "directx", "dxwebsetup",
    "unins000", "unins001", "uninstall", "wdfcoinstaller", "dpinst", "7z.exe", "7z.dll"
}


def should_skip_scan(file_path: Path, original_filename: Optional[str] = None) -> bool:
    """
    Context-aware asset filter:
    - Pure media, fonts, styles, docs -> always skipped.
    - Third-party redistributables / system runtimes (VC++, .NET, DirectX, iot_driver) -> always skipped.
    - XAML / QML / BAML / CML -> skipped in UI / skin / theme / localization contexts,
      but retained when located in technical device / config / protocol / firmware contexts.
    """
    ref_name = (original_filename or file_path.name).lower()
    suffix = Path(ref_name).suffix.lower()

    if suffix in ALWAYS_SKIP_EXTENSIONS:
        return True

    # Skip bundled runtime installers and system redistributables
    if any(term in ref_name for term in THIRD_PARTY_BUNDLE_TERMS):
        return True

    full_path_str = str(file_path).lower().replace("\\", "/")
    if any(p in full_path_str for p in ["/redist/", "/vcredist/", "/directx/", "/driver_installers/"]):
        return True

    if suffix in MARKUP_UI_EXTENSIONS:
        # If in technical context, retain for scanning
        if any(term in full_path_str for term in TECH_CONTEXT_TERMS):
            return False
        # If in UI / theme / resource context or default layout, skip
        if any(term in full_path_str for term in UI_RESOURCE_TERMS):
            return True
        return True

    return False



class ScannerDispatcher:
    def __init__(self):
        self.inf_scanner = InfScanner()
        self.json_scanner = JsonScanner()
        self.js_scanner = JsScanner()
        self.xml_scanner = XmlScanner()
        self.sqlite_scanner = SqliteScanner()
        self.text_scanner = TextScanner()
        self.binary_scanner = BinaryStringsScanner()

    def scan_file(
        self,
        file_path: Path,
        artifact_sha256: str,
        product_id: int | None = None,
        original_filename: Optional[str] = None
    ) -> AggregatedScanResult:
        """Route file to appropriate static scanner."""
        if should_skip_scan(file_path, original_filename=original_filename):
            return AggregatedScanResult([], [], [], [])

        ft = detect_file_type(file_path, original_filename=original_filename)
        
        identifiers: list[DeviceIdentifierFact] = []
        hints: list[ProtocolHintFact] = []
        facts: list[GenericFact] = []
        device_records: list[ParsedDeviceRecord] = []

        if ft == FileType.INF:
            res = self.inf_scanner.scan_file(file_path, artifact_sha256, product_id)
            identifiers.extend(res.identifiers)
            facts.extend(res.facts)
        elif ft == FileType.JSON:
            res = self.json_scanner.scan_file(file_path, artifact_sha256, product_id)
            identifiers.extend(res.identifiers)
            hints.extend(res.hints)
            facts.extend(res.facts)
            device_records.extend(res.device_records)
        elif ft == FileType.JS:
            res = self.js_scanner.scan_file(file_path, artifact_sha256, product_id)
            identifiers.extend(res.identifiers)
            hints.extend(res.hints)
            facts.extend(res.facts)
            device_records.extend(res.device_records)
        elif ft == FileType.XML:
            res = self.xml_scanner.scan_file(file_path, artifact_sha256, product_id)
            identifiers.extend(res.identifiers)
            hints.extend(res.hints)
            facts.extend(res.facts)
        elif ft == FileType.SQLITE:
            res = self.sqlite_scanner.scan_file(file_path, artifact_sha256, product_id)
            identifiers.extend(res.identifiers)
            hints.extend(res.hints)
            facts.extend(res.facts)
        elif ft == FileType.INI or ft == FileType.TEXT:
            res = self.text_scanner.scan_file(file_path, artifact_sha256, product_id)
            identifiers.extend(res.identifiers)
            hints.extend(res.hints)
            facts.extend(res.facts)
        elif ft == FileType.PE_EXE or ft == FileType.MSI:
            res = self.binary_scanner.scan_file(file_path, artifact_sha256, product_id)
            identifiers.extend(res.identifiers)
            hints.extend(res.hints)
            facts.extend(res.facts)
            device_records.extend(res.device_records)
        else:
            try_json = self.json_scanner.scan_file(file_path, artifact_sha256, product_id)
            if try_json.identifiers or try_json.hints or try_json.device_records:
                identifiers.extend(try_json.identifiers)
                hints.extend(try_json.hints)
                facts.extend(try_json.facts)
                device_records.extend(try_json.device_records)
            else:
                try_bin = self.binary_scanner.scan_file(file_path, artifact_sha256, product_id)
                identifiers.extend(try_bin.identifiers)
                hints.extend(try_bin.hints)
                facts.extend(try_bin.facts)
                device_records.extend(try_bin.device_records)

        return AggregatedScanResult(identifiers, hints, facts, device_records)
