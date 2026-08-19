"""XML and AppxManifest scanner for hardware descriptors."""

import re
from pathlib import Path
from typing import NamedTuple
import xml.etree.ElementTree as ET

from ingest.logging_setup import log_scan, get_logger
from ingest.normalize.evidence import DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
from ingest.normalize.identifiers import extract_vid_pid_from_text

logger = get_logger()


class XmlScanResult(NamedTuple):
    identifiers: list[DeviceIdentifierFact]
    hints: list[ProtocolHintFact]
    facts: list[GenericFact]


class XmlScanner:
    def scan_file(self, file_path: Path, artifact_sha256: str, product_id: int | None = None) -> XmlScanResult:
        identifiers: list[DeviceIdentifierFact] = []
        hints: list[ProtocolHintFact] = []
        facts: list[GenericFact] = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            if not content.strip():
                return XmlScanResult(identifiers, hints, facts)

            log_scan(f"Scanning XML file: {file_path.name}")

            # 1. Regex scan for standard VID/PID
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

            # 2. Try XML DOM parsing to find specific tags
            try:
                # Strip namespaces for easier querying
                xml_clean = re.sub(r'\sxmlns(:\w+)?="[^"]+"', '', content, count=0)
                root = ET.fromstring(xml_clean)

                for elem in root.iter():
                    tag = elem.tag.lower()
                    text = (elem.text or "").strip()
                    attribs = elem.attrib

                    if "product" in tag or "device" in tag or "model" in tag:
                        if text and len(text) < 60:
                            facts.append(GenericFact(
                                product_id=product_id,
                                key=f"xml_{tag}",
                                value=text,
                                artifact_sha256=artifact_sha256,
                                evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                                confidence=0.85
                            ))
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"[scan] Error parsing XML {file_path.name}: {e}")

        return XmlScanResult(identifiers, hints, facts)
