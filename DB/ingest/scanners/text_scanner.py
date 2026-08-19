"""Text configuration scanner (INI, TOML, YAML, CFG, TXT)."""

import re
from pathlib import Path
from typing import NamedTuple

from ingest.logging_setup import log_scan, get_logger
from ingest.normalize.evidence import DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
from ingest.normalize.identifiers import extract_vid_pid_from_text

logger = get_logger()


class TextScanResult(NamedTuple):
    identifiers: list[DeviceIdentifierFact]
    hints: list[ProtocolHintFact]
    facts: list[GenericFact]


class TextScanner:
    def scan_file(self, file_path: Path, artifact_sha256: str, product_id: int | None = None) -> TextScanResult:
        identifiers: list[DeviceIdentifierFact] = []
        hints: list[ProtocolHintFact] = []
        facts: list[GenericFact] = []

        try:
            for enc in ["utf-8", "latin-1", "gbk", "cp1252"]:
                try:
                    with open(file_path, "r", encoding=enc, errors="replace") as f:
                        content = f.read()
                    break
                except Exception:
                    continue

            if not content.strip():
                return TextScanResult(identifiers, hints, facts)

            log_scan(f"Scanning config text file: {file_path.name}")

            # 1. VID/PID extraction
            vid_pids = extract_vid_pid_from_text(content)
            for item in vid_pids:
                identifiers.append(DeviceIdentifierFact(
                    product_id=product_id,
                    vid=item.vid,
                    pid=item.pid,
                    vid_hex=item.vid_hex,
                    pid_hex=item.pid_hex,
                    artifact_sha256=artifact_sha256,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.90
                ))

            # 2. Key-value hints like Protocol=..., Chipset=...
            for line in content.splitlines():
                if "=" in line:
                    parts = line.split("=", 1)
                    k, v = parts[0].strip().lower(), parts[1].strip()
                    if k in ["protocol", "chip", "mcu", "sensor", "firmware", "device_model"]:
                        hints.append(ProtocolHintFact(
                            product_id=product_id,
                            hint_key=k,
                            hint_value=v,
                            artifact_sha256=artifact_sha256,
                            evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                            confidence=0.85
                        ))

        except Exception as e:
            logger.debug(f"[scan] Error in TextScanner for {file_path.name}: {e}")

        return TextScanResult(identifiers, hints, facts)
