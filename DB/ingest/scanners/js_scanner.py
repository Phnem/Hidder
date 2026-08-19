"""JavaScript scanner for Web Configurators and Electron bundles with structured device record extraction."""

import re
from pathlib import Path
from typing import NamedTuple

from ingest.logging_setup import log_scan, get_logger
from ingest.normalize.evidence import DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
from ingest.normalize.identifiers import extract_vid_pid_from_text, normalize_vid_pid
from ingest.scanners.json_scanner import ParsedDeviceRecord

logger = get_logger()

# Regex patterns for JS objects
RE_JS_HID_FILTER = re.compile(
    r'vendorId\s*:\s*(?:0x([0-9a-fA-F]+)|([0-9]+))\s*,\s*productId\s*:\s*(?:0x([0-9a-fA-F]+)|([0-9]+))',
    re.IGNORECASE
)

RE_JS_USAGE = re.compile(
    r'usagePage\s*:\s*(?:0x([0-9a-fA-F]+)|([0-9]+))\s*,\s*usage\s*:\s*(?:0x([0-9a-fA-F]+)|([0-9]+))',
    re.IGNORECASE
)

RE_JS_SDK_NAME = re.compile(
    r'(?:sdkModuleName|protocolModule|deviceProtocol|driverEngine)\s*[:=]\s*["\']([^"\']+)["\']',
    re.IGNORECASE
)

RE_JS_COMMANDS = re.compile(
    r'\b(?:CMD_[A-Z0-9_]+|setPollingRate|getKeyTravel|DevSetAxisCfg|setDebounce|setDpiLevel|setLedMode)\b'
)


class JsScanResult(NamedTuple):
    identifiers: list[DeviceIdentifierFact]
    hints: list[ProtocolHintFact]
    facts: list[GenericFact]
    device_records: list[ParsedDeviceRecord] = []


class JsScanner:
    def scan_file(self, file_path: Path, artifact_sha256: str, product_id: int | None = None) -> JsScanResult:
        identifiers: list[DeviceIdentifierFact] = []
        hints: list[ProtocolHintFact] = []
        facts: list[GenericFact] = []
        device_records: list[ParsedDeviceRecord] = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            if not content.strip():
                return JsScanResult(identifiers, hints, facts, device_records)

            log_scan(f"Scanning JavaScript bundle: {file_path.name}")

            # 1. Extract structured device records from object literals:
            # { ... vendorId/vid: ..., productId/pid: ..., usagePage: ..., usage: ..., name/model/firmwareMark: ... }
            for m in re.finditer(r'\{([^{}]+(?:vendorId|vid)\s*:\s*(?:0x[0-9a-fA-F]+|\d+)[^{}]+)\}', content):
                chunk = m.group(1)
                if "productId" in chunk or "pid" in chunk:
                    vid_m = re.search(r'(?:vendorId|vid)\s*:\s*(?:0x([0-9a-fA-F]+)|(\d+))', chunk)
                    pid_m = re.search(r'(?:productId|pid)\s*:\s*(?:0x([0-9a-fA-F]+)|(\d+))', chunk)
                    if vid_m and pid_m:
                        v_val = int(vid_m.group(1), 16) if vid_m.group(1) else int(vid_m.group(2))
                        p_val = int(pid_m.group(1), 16) if pid_m.group(1) else int(pid_m.group(2))
                        norm = normalize_vid_pid(v_val, p_val)
                        if norm:
                            name_m = re.search(r'(?:firmwareMark|name|model|deviceName|device|title|label|productName)\s*:\s*["\']([^"\']+)["\']', chunk)
                            usage_m = re.search(r'usage\s*:\s*(?:0x([0-9a-fA-F]+)|(\d+))', chunk)
                            up_m = re.search(r'usagePage\s*:\s*(?:0x([0-9a-fA-F]+)|(\d+))', chunk)

                            dev_name = name_m.group(1).strip() if name_m else ""
                            u_page = (int(up_m.group(1), 16) if up_m.group(1) else int(up_m.group(2))) if up_m else None
                            u_val = (int(usage_m.group(1), 16) if usage_m.group(1) else int(usage_m.group(2))) if usage_m else None

                            scoped_hints = {}
                            if u_page is not None and u_val is not None:
                                scoped_hints["hid_usage"] = f"UsagePage:0x{u_page:04X}, Usage:0x{u_val:04X}"
                            if "8000" in chunk or "8k" in dev_name.lower():
                                scoped_hints["pollingRateMax"] = "8000"
                            if "paw3950" in chunk.lower() or "paw3950" in dev_name.lower():
                                scoped_hints["sensor"] = "PAW3950"
                            elif "paw3395" in chunk.lower() or "paw3395" in dev_name.lower():
                                scoped_hints["sensor"] = "PAW3395"

                            rec = ParsedDeviceRecord(
                                name=dev_name or f"Device {norm.vid_hex}:{norm.pid_hex}",
                                model=dev_name or "",
                                vid=norm.vid,
                                pid=norm.pid,
                                vid_hex=norm.vid_hex,
                                pid_hex=norm.pid_hex,
                                usage_page=u_page,
                                usage=u_val,
                                hints=scoped_hints,
                                confidence=0.95
                            )
                            device_records.append(rec)

            # 2. Extract class/constructor device definitions:
            # this.vendorId=13364, this.productId=53321, this.productName="M6 8K", ...
            for m in re.finditer(r'this\.vendorId\s*=\s*(?:0x([0-9a-fA-F]+)|(\d+))\s*,\s*this\.productId\s*=\s*(?:0x([0-9a-fA-F]+)|(\d+))(?:[^{};]{0,100}this\.productName\s*=\s*["\']([^"\']+)["\'])?', content):
                v_hex, v_dec, p_hex, p_dec, prod_name = m.groups()
                vid_val = int(v_hex, 16) if v_hex else int(v_dec)
                pid_val = int(p_hex, 16) if p_hex else int(p_dec)
                norm = normalize_vid_pid(vid_val, pid_val)
                if norm:
                    scoped_hints = {}
                    if prod_name and "8k" in prod_name.lower():
                        scoped_hints["pollingRateMax"] = "8000"
                    rec = ParsedDeviceRecord(
                        name=prod_name or f"Device {norm.vid_hex}:{norm.pid_hex}",
                        model=prod_name or "",
                        vid=norm.vid,
                        pid=norm.pid,
                        vid_hex=norm.vid_hex,
                        pid_hex=norm.pid_hex,
                        hints=scoped_hints,
                        confidence=0.95
                    )
                    device_records.append(rec)

            # 3. Look for WebHID/WebUSB filter objects (global fallback)
            for m in RE_JS_HID_FILTER.finditer(content):
                v_hex, v_dec, p_hex, p_dec = m.groups()
                vid_val = int(v_hex, 16) if v_hex else int(v_dec)
                pid_val = int(p_hex, 16) if p_hex else int(p_dec)
                norm = normalize_vid_pid(vid_val, pid_val)
                if norm and not any(i.vid == norm.vid and i.pid == norm.pid for i in identifiers):
                    identifiers.append(DeviceIdentifierFact(
                        product_id=product_id,
                        vid=norm.vid,
                        pid=norm.pid,
                        vid_hex=norm.vid_hex,
                        pid_hex=norm.pid_hex,
                        artifact_sha256=artifact_sha256,
                        evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                        confidence=0.95
                    ))

            # 4. Usage & UsagePage pairs
            for m in RE_JS_USAGE.finditer(content):
                up_hex, up_dec, u_hex, u_dec = m.groups()
                up_val = int(up_hex, 16) if up_hex else int(up_dec)
                u_val = int(u_hex, 16) if u_hex else int(u_dec)
                hints.append(ProtocolHintFact(
                    product_id=product_id,
                    hint_key="hid_usage",
                    hint_value=f"UsagePage:0x{up_val:04X}, Usage:0x{u_val:04X}",
                    artifact_sha256=artifact_sha256,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.90
                ))

            # 5. SDK / Protocol hints
            for m in RE_JS_SDK_NAME.finditer(content):
                sdk_name = m.group(1).strip()
                hints.append(ProtocolHintFact(
                    product_id=product_id,
                    hint_key="sdkModuleName",
                    hint_value=sdk_name,
                    artifact_sha256=artifact_sha256,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.85
                ))

            # 6. Protocol Command functions/opcodes
            found_cmds = set(RE_JS_COMMANDS.findall(content))
            for cmd in list(found_cmds)[:10]:
                hints.append(ProtocolHintFact(
                    product_id=product_id,
                    hint_key="command_signature",
                    hint_value=cmd,
                    artifact_sha256=artifact_sha256,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.75
                ))

        except Exception as e:
            logger.debug(f"[scan] Error scanning JS {file_path.name}: {e}")

        return JsScanResult(identifiers, hints, facts, device_records)
