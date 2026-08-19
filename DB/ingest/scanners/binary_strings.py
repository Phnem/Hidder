"""Static binary strings extractor (ASCII / UTF-8 / UTF-16LE) for PE/DLL/SYS/installer binaries."""

import re
from pathlib import Path
from typing import NamedTuple

from ingest.logging_setup import log_scan, get_logger
from ingest.normalize.evidence import DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
from ingest.normalize.identifiers import extract_vid_pid_from_text
from ingest.scanners.json_scanner import ParsedDeviceRecord

logger = get_logger()

# Regex for ASCII and UTF-16LE strings >= 4 chars
RE_ASCII_STRINGS = re.compile(rb'[\x20-\x7E]{4,}')
RE_UTF16_STRINGS = re.compile(rb'(?:[\x20-\x7E]\x00){4,}')


class BinaryScanResult(NamedTuple):
    identifiers: list[DeviceIdentifierFact]
    hints: list[ProtocolHintFact]
    facts: list[GenericFact]
    device_records: list[ParsedDeviceRecord] = []


class BinaryStringsScanner:
    def scan_file(
        self,
        file_path: Path,
        artifact_sha256: str,
        product_id: int | None = None,
        max_bytes: int = 40 * 1024 * 1024
    ) -> BinaryScanResult:
        identifiers: list[DeviceIdentifierFact] = []
        hints: list[ProtocolHintFact] = []
        facts: list[GenericFact] = []
        device_records: list[ParsedDeviceRecord] = []

        try:
            if not file_path.exists():
                return BinaryScanResult(identifiers, hints, facts, device_records)

            size = file_path.stat().st_size
            log_scan(f"Static binary inspection: {file_path.name} ({size / 1024 / 1024:.2f} MB)")

            with open(file_path, "rb") as f:
                data = f.read(max_bytes)

            # 1. Extract ASCII strings
            ascii_strings = [m.group(0).decode("latin-1", errors="ignore") for m in RE_ASCII_STRINGS.finditer(data)]
            # 2. Extract UTF-16LE strings
            utf16_strings = [m.group(0).decode("utf-16le", errors="ignore") for m in RE_UTF16_STRINGS.finditer(data)]

            all_extracted = ascii_strings + utf16_strings
            corpus = "\n".join(all_extracted)
            corpus_lower = corpus.lower()

            # 3. Detect Installer / Container format
            if b"Inno Setup" in data:
                facts.append(GenericFact(
                    product_id=product_id,
                    key="installer_format",
                    value="Inno Setup",
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.95
                ))
            elif b"Nullsoft" in data or b"NSIS" in data:
                facts.append(GenericFact(
                    product_id=product_id,
                    key="installer_format",
                    value="NSIS",
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.95
                ))

            # 4. Extract embedded titles/product strings from binary resources
            found_titles = set()
            for s in all_extracted:
                s_strip = s.strip()
                if any(k in s_strip.lower() for k in ["aula", "atk", "vxe", "epomaker", "keychron"]) and len(s_strip) < 60:
                    if not any(noise in s_strip.lower() for noise in ["http", ".com", ".png", ".dll", "copyright", "microsoft"]):
                        found_titles.add(s_strip)

            for t in list(found_titles)[:3]:
                facts.append(GenericFact(
                    product_id=product_id,
                    key="embedded_binary_title",
                    value=t,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.85
                ))

            # 5. Extract VID/PIDs
            vid_pids = extract_vid_pid_from_text(corpus)
            for item in vid_pids:
                identifiers.append(DeviceIdentifierFact(
                    product_id=product_id,
                    vid=item.vid,
                    pid=item.pid,
                    vid_hex=item.vid_hex,
                    pid_hex=item.pid_hex,
                    artifact_sha256=artifact_sha256,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.85
                ))

            # 6. Check for known SDK / driver signatures
            for candidate in ["bytech", "sinowealth", "sonix", "holtek", "nordic", "compx", "yichip", "panchip", "realtek", "telink"]:
                if candidate in corpus_lower:
                    hints.append(ProtocolHintFact(
                        product_id=product_id,
                        hint_key="mcu_sdk_signature",
                        hint_value=candidate,
                        artifact_sha256=artifact_sha256,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.75,
                        context=f"Detected string signature '{candidate}' in {file_path.name}"
                    ))

            # 7. Check for sensor mentions
            for sensor in ["paw3950", "paw3395", "paw3311", "pmw3389", "a3050", "pixart"]:
                if sensor in corpus_lower:
                    hints.append(ProtocolHintFact(
                        product_id=product_id,
                        hint_key="sensor_signature",
                        hint_value=sensor.upper(),
                        artifact_sha256=artifact_sha256,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.80,
                        context=f"Detected sensor name '{sensor.upper()}' in {file_path.name}"
                    ))

        except Exception as e:
            logger.debug(f"[scan] Binary scan error for {file_path.name}: {e}")

        return BinaryScanResult(identifiers, hints, facts, device_records)
