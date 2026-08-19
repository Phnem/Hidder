"""JSON/JSON5 device definition and manifest scanner with strict device-level hint scoping."""

import json
import re
from pathlib import Path
from typing import Any, NamedTuple
from pydantic import BaseModel, Field

from ingest.logging_setup import log_scan, get_logger
from ingest.normalize.evidence import DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
from ingest.normalize.identifiers import normalize_vid_pid, VidPid

logger = get_logger()


class ParsedDeviceRecord(BaseModel):
    name: str = ""
    model: str = ""
    vid: int
    pid: int
    vid_hex: str
    pid_hex: str
    usage_page: int | None = None
    usage: int | None = None
    manufacturer: str | None = None
    category: str | None = None
    hints: dict[str, str] = Field(default_factory=dict)
    confidence: float = 1.0


class JsonScanResult(NamedTuple):
    identifiers: list[DeviceIdentifierFact]
    hints: list[ProtocolHintFact]
    facts: list[GenericFact]
    device_records: list[ParsedDeviceRecord] = []


class JsonScanner:
    def scan_file(self, file_path: Path, artifact_sha256: str, product_id: int | None = None) -> JsonScanResult:
        identifiers: list[DeviceIdentifierFact] = []
        hints: list[ProtocolHintFact] = []
        facts: list[GenericFact] = []
        device_records: list[ParsedDeviceRecord] = []

        # Skip tool flasher config files that contain generic MCU/bootloader dictionaries
        if file_path.name.lower() in {"support_config.json", "tool_config.json", "flasher_config.json", "upgrade_config.json"}:
            return JsonScanResult(identifiers, hints, facts, device_records)

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                raw_text = f.read()

            if not raw_text.strip():
                return JsonScanResult(identifiers, hints, facts, device_records)

            cleaned = re.sub(r'//.*?\n|/\*.*?\*/', '', raw_text, flags=re.S)
            data = json.loads(cleaned)

            log_scan(f"Scanning JSON configuration: {file_path.name}")
            self._walk_json(data, artifact_sha256, product_id, identifiers, hints, facts, device_records, file_name=file_path.name)

        except Exception as e:
            logger.debug(f"[scan] Could not parse JSON in {file_path.name}: {e}")

        return JsonScanResult(identifiers, hints, facts, device_records)

    def _walk_json(
        self,
        node: Any,
        sha256: str,
        product_id: int | None,
        identifiers: list[DeviceIdentifierFact],
        hints: list[ProtocolHintFact],
        facts: list[GenericFact],
        device_records: list[ParsedDeviceRecord],
        file_name: str = ""
    ):
        if isinstance(node, dict):
            vid, pid, norm = None, None, None
            name_val = node.get("name") or node.get("productName") or node.get("device_name") or node.get("keyboard_name") or ""
            model_val = node.get("model") or node.get("product_name") or ""

            if "vendorProductId" in node:
                vpid_val = node["vendorProductId"]
                try:
                    vpid_int = int(vpid_val, 16 if str(vpid_val).startswith("0x") else 10)
                    norm = normalize_vid_pid((vpid_int >> 16) & 0xFFFF, vpid_int & 0xFFFF)
                except Exception:
                    pass

            if not norm:
                vid_key = next((k for k in ["vendorId", "vendor_id", "vid", "vId", "VendorId"] if k in node), None)
                pid_key = next((k for k in ["productId", "product_id", "pid", "pId", "ProductId"] if k in node), None)
                if vid_key and pid_key:
                    norm = normalize_vid_pid(node[vid_key], node[pid_key])

            scoped_hints: dict[str, str] = {}
            for hk in ["sdkModuleName", "protocolModule", "protocol", "chipset", "mcu", "sensor", "pollingRateMax", "firmwareVersion"]:
                if hk in node and node[hk] is not None:
                    scoped_hints[hk] = str(node[hk])
                    if name_val or model_val or product_id is not None:
                        hints.append(ProtocolHintFact(
                            product_id=product_id,
                            hint_key=hk,
                            hint_value=str(node[hk]),
                            artifact_sha256=sha256,
                            evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                            confidence=0.9
                        ))

            # Only emit device record / identifier if valid norm AND (has name/model OR is a dedicated single-device JSON)
            if norm and (name_val or model_val or "info.json" in file_name.lower() or "config.json" in file_name.lower()):
                up = node.get("usagePage") or node.get("usage_page")
                u = node.get("usage")
                up_int = int(up) if up is not None and str(up).isdigit() else None
                u_int = int(u) if u is not None and str(u).isdigit() else None

                dev_record = ParsedDeviceRecord(
                    name=str(name_val),
                    model=str(model_val),
                    vid=norm.vid,
                    pid=norm.pid,
                    vid_hex=norm.vid_hex,
                    pid_hex=norm.pid_hex,
                    usage_page=up_int,
                    usage=u_int,
                    manufacturer=node.get("manufacturer") or node.get("vendorName"),
                    category=node.get("category"),
                    hints=scoped_hints
                )
                device_records.append(dev_record)

                identifiers.append(DeviceIdentifierFact(
                    product_id=product_id,
                    vid=norm.vid,
                    pid=norm.pid,
                    vid_hex=norm.vid_hex,
                    pid_hex=norm.pid_hex,
                    product_string=str(name_val or model_val),
                    manufacturer_string=node.get("manufacturer") or node.get("vendorName"),
                    usage_page=up_int,
                    usage=u_int,
                    artifact_sha256=sha256,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=1.0
                ))

            # Recursively search child structures
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    self._walk_json(v, sha256, product_id, identifiers, hints, facts, device_records, file_name=file_name)

        elif isinstance(node, list):
            for item in node:
                self._walk_json(item, sha256, product_id, identifiers, hints, facts, device_records, file_name=file_name)

