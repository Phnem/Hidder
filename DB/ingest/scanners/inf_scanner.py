"""INF driver file scanner for Windows hardware IDs and device descriptions."""

import re
from pathlib import Path
from typing import NamedTuple

from ingest.normalize.evidence import DeviceIdentifierFact, GenericFact, EvidenceLevel
from ingest.normalize.identifiers import extract_vid_pid_from_text, VidPid
from ingest.logging_setup import log_scan, get_logger

logger = get_logger()

RE_INF_SECTION = re.compile(r'^\s*\[([^\]]+)\]', re.MULTILINE)
RE_KEY_VAL = re.compile(r'^\s*([^=;]+?)\s*=\s*([^;]*?)\s*(?:;.*)?$', re.MULTILINE)


class InfScanResult(NamedTuple):
    identifiers: list[DeviceIdentifierFact]
    facts: list[GenericFact]


class InfScanner:
    def scan_file(self, file_path: Path, artifact_sha256: str, product_id: int | None = None) -> InfScanResult:
        """Parse an INF file for USB/HID hardware IDs and device strings."""
        identifiers: list[DeviceIdentifierFact] = []
        facts: list[GenericFact] = []

        try:
            # Try UTF-16 then UTF-8 / Latin-1
            content = ""
            for enc in ["utf-8", "utf-16", "latin-1", "cp1252"]:
                try:
                    with open(file_path, "r", encoding=enc, errors="replace") as f:
                        content = f.read()
                    break
                except Exception:
                    continue

            if not content:
                return InfScanResult(identifiers, facts)

            log_scan(f"Scanning INF driver file: {file_path.name}")

            # 1. Parse string replacements [Strings] section if present
            strings_map: dict[str, str] = {}
            current_section = ""
            for line in content.splitlines():
                sec_match = RE_INF_SECTION.match(line)
                if sec_match:
                    current_section = sec_match.group(1).strip().lower()
                    continue
                if current_section == "strings":
                    kv = RE_KEY_VAL.match(line)
                    if kv:
                        k, v = kv.group(1).strip().strip('"'), kv.group(2).strip().strip('"')
                        strings_map[f"%{k.lower()}%"] = v

            # 2. Extract VID/PIDs and line contexts
            vid_pids = extract_vid_pid_from_text(content)
            for item in vid_pids:
                # Find device description on the same line if available
                dev_desc = None
                for line in content.splitlines():
                    if f"{item.vid_hex[2:]}&PID_{item.pid_hex[2:]}".lower() in line.lower():
                        parts = line.split("=", 1)
                        if len(parts) > 1:
                            raw_desc = parts[0].strip().strip('"')
                            # Resolve %String%
                            dev_desc = strings_map.get(raw_desc.lower(), raw_desc)
                            break

                fact = DeviceIdentifierFact(
                    product_id=product_id,
                    vid=item.vid,
                    pid=item.pid,
                    vid_hex=item.vid_hex,
                    pid_hex=item.pid_hex,
                    product_string=dev_desc,
                    artifact_sha256=artifact_sha256,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=1.0
                )
                identifiers.append(fact)

            # 3. Extract DriverVer if present
            driver_ver_match = re.search(r'DriverVer\s*=\s*([^;\r\n]+)', content, re.IGNORECASE)
            if driver_ver_match:
                ver_val = driver_ver_match.group(1).strip().strip('"')
                facts.append(GenericFact(
                    product_id=product_id,
                    key="driver_version_inf",
                    value=ver_val,
                    artifact_sha256=artifact_sha256,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.9
                ))

        except Exception as e:
            logger.error(f"[scan] Error scanning INF {file_path.name}: {e}", exc_info=True)

        return InfScanResult(identifiers, facts)
