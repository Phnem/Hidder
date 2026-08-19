"""Conservative text/JSON/INF extraction for the first static-analysis tier."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from miner import __version__
from miner.schemas.models import ConfidenceClass, Observation

MAX_TEXT_BYTES = 25 * 1024 * 1024
_VID_PID = re.compile(r"vendorId\s*[:=]\s*(0x[0-9a-fA-F]+|\d+).{0,260}?productId\s*[:=]\s*(0x[0-9a-fA-F]+|\d+)", re.DOTALL)
_VID_PID_REVERSE = re.compile(r"productId\s*[:=]\s*(0x[0-9a-fA-F]+|\d+).{0,260}?vendorId\s*[:=]\s*(0x[0-9a-fA-F]+|\d+)", re.DOTALL)
_HID_SINK = re.compile(r"\b(?:sendReport|sendFeatureReport)\s*\(\s*(0x[0-9a-fA-F]+|\d+)")
_USB_SINK = re.compile(r"\b(?:transferIn|transferOut|controlTransferIn|controlTransferOut)\s*\(")
_INF_ID = re.compile(r"\bVID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})\b")
_DIRECT_PACKET = re.compile(r"\b(sendReport|sendFeatureReport)\s*\(\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*new\s+Uint8Array\s*\(\s*\[([^\]]{1,2048})\]", re.DOTALL)
_DANGEROUS = re.compile(r"\b(firmware|flash|bootloader|erase|factory\s*reset|calibrat(?:e|ion)|\bdfu\b)\b", re.IGNORECASE)
_ASCII_STRING = re.compile(rb"[\x20-\x7e]{4,}")
_TRANSPORT_TOKENS = {b"HidD_SetFeature", b"HidD_GetFeature", b"HidD_GetInputReport", b"WriteFile", b"ReadFile", b"hid_write", b"hid_send_feature_report", b"node-hid", b"hidapi", b"libusb", b"WinUSB"}


def _number(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            return None
    return None


def _observation_id(sha256: str, source_path: str, kind: str, value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    token = f"{sha256}|{source_path}|{kind}|{canonical}".encode()
    return f"obs-{hashlib.sha256(token).hexdigest()[:20]}"


def _make(sha256: str, source_path: str, kind: str, value: Any, confidence: ConfidenceClass) -> Observation:
    return Observation(
        observation_id=_observation_id(sha256, source_path, kind, value), artifact_sha256=sha256,
        extractor="static.tier1", extractor_version=__version__, kind=kind, value=value,
        source_path=source_path, confidence=confidence,
    )


def _walk_json(value: Any, path: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _walk_json(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, f"{path}[{index}]")


def scan_json(sha256: str, source_path: str, text: str) -> list[Observation]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    results: list[Observation] = []
    for location, node in _walk_json(payload):
        vid = _number(node.get("vendorId", node.get("vendor_id")))
        pid = _number(node.get("productId", node.get("product_id")))
        if vid is not None and pid is not None and 0 <= vid <= 0xFFFF and 0 <= pid <= 0xFFFF:
            value = {"vid": vid, "pid": pid, "context": location}
            results.append(_make(sha256, f"{source_path}:{location}", "identity.vid_pid", value, ConfidenceClass.VERIFIED_STRUCTURED_MAPPING))
        usage_page = _number(node.get("usagePage"))
        usage = _number(node.get("usage"))
        if usage_page is not None and usage is not None:
            results.append(_make(sha256, f"{source_path}:{location}", "topology.usage", {"usage_page": usage_page, "usage": usage}, ConfidenceClass.VERIFIED_STRUCTURED_MAPPING))
        if vid is not None and pid is not None and isinstance(node.get("layouts"), dict):
            results.append(_make(sha256, f"{source_path}:{location}", "ecosystem.via_qmk", {"kind": "via_definition"}, ConfidenceClass.VERIFIED_STRUCTURED_MAPPING))
        dependencies = node.get("dependencies")
        if isinstance(dependencies, dict) and "electron" in dependencies:
            results.append(_make(sha256, f"{source_path}:{location}.dependencies.electron", "artifact.electron", {"package": "electron"}, ConfidenceClass.VERIFIED_STRUCTURED_MAPPING))
    return results


def scan_javascript(sha256: str, source_path: str, text: str) -> list[Observation]:
    from miner.static.js_flow import scan_simple_buffer_flows

    results: list[Observation] = []
    pairs = [(match.group(1), match.group(2)) for match in _VID_PID.finditer(text)]
    pairs.extend((match.group(2), match.group(1)) for match in _VID_PID_REVERSE.finditer(text))
    for vid_raw, pid_raw in pairs:
        vid, pid = _number(vid_raw), _number(pid_raw)
        if vid is not None and pid is not None and 0 <= vid <= 0xFFFF and 0 <= pid <= 0xFFFF:
            results.append(_make(sha256, source_path, "identity.vid_pid", {"vid": vid, "pid": pid, "context": "WebHID/WebUSB filter literal"}, ConfidenceClass.VERIFIED_SOURCE_CODE))
    for match in _HID_SINK.finditer(text):
        report_id = _number(match.group(1))
        if report_id is not None:
            results.append(_make(sha256, f"{source_path}:byte={match.start()}", "topology.hid_sink", {"method": match.group(0).split("(")[0], "report_id": report_id}, ConfidenceClass.VERIFIED_SOURCE_CODE))
    for match in _USB_SINK.finditer(text):
        results.append(_make(sha256, f"{source_path}:byte={match.start()}", "topology.usb_sink", {"method": match.group(0).split("(")[0]}, ConfidenceClass.VERIFIED_SOURCE_CODE))
    for match in _DIRECT_PACKET.finditer(text):
        try:
            payload = [_number(part.strip()) for part in match.group(3).split(",")]
        except ValueError:
            payload = []
        if payload and all(item is not None and 0 <= item <= 255 for item in payload):
            results.append(_make(sha256, f"{source_path}:byte={match.start()}", "protocol.direct_packet_literal", {
                "method": match.group(1), "report_id": _number(match.group(2)), "bytes": payload,
            }, ConfidenceClass.VERIFIED_SOURCE_CODE))
    for match in _DANGEROUS.finditer(text):
        results.append(_make(sha256, f"{source_path}:byte={match.start()}", "protocol.dangerous_hint", {"keyword": match.group(1)}, ConfidenceClass.VERIFIED_SOURCE_CODE))
    results.extend(scan_simple_buffer_flows(sha256, source_path, text))
    return results


def scan_inf(sha256: str, source_path: str, text: str) -> list[Observation]:
    return [_make(sha256, f"{source_path}:byte={match.start()}", "identity.vid_pid", {
        "vid": int(match.group(1), 16), "pid": int(match.group(2), 16), "context": "INF hardware ID; not product-wide mapping",
    }, ConfidenceClass.VERIFIED_VENDOR_ARTIFACT) for match in _INF_ID.finditer(text)]


def scan_sourcemap(sha256: str, source_path: str, text: str) -> list[Observation]:
    try:
        source_map = json.loads(text)
    except json.JSONDecodeError:
        return []
    sources = source_map.get("sources", [])
    contents = source_map.get("sourcesContent", [])
    if not isinstance(sources, list) or not isinstance(contents, list):
        return []
    results: list[Observation] = []
    for name, content in zip(sources, contents):
        if isinstance(name, str) and isinstance(content, str):
            results.extend(scan_javascript(sha256, f"{source_path}::sourcesContent/{name}", content))
    return results


def scan_binary(sha256: str, source_path: str, path: Path) -> list[Observation]:
    if path.stat().st_size > MAX_TEXT_BYTES:
        return []
    raw = path.read_bytes()
    found = sorted(token.decode("ascii") for token in _TRANSPORT_TOKENS if token in raw)
    results = [_make(sha256, source_path, "native.transport_hint", {"token": token}, ConfidenceClass.VERIFIED_VENDOR_ARTIFACT) for token in found]
    for match in _DANGEROUS.finditer("\n".join(item.decode("ascii", errors="ignore") for item in _ASCII_STRING.findall(raw))):
        results.append(_make(sha256, f"{source_path}:string", "protocol.dangerous_hint", {"keyword": match.group(1)}, ConfidenceClass.VERIFIED_VENDOR_ARTIFACT))
    return results


def scan_file(sha256: str, source_path: str, path: Path) -> list[Observation]:
    if path.stat().st_size > MAX_TEXT_BYTES:
        return []
    suffix = Path(source_path).suffix.lower()
    if suffix in {".exe", ".dll", ".sys", ".node"}:
        return scan_binary(sha256, source_path, path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    if suffix in {".json", ".json5"}:
        return scan_json(sha256, source_path, text)
    if suffix in {".js", ".mjs", ".cjs", ".ts"}:
        return scan_javascript(sha256, source_path, text)
    if suffix == ".inf":
        return scan_inf(sha256, source_path, text)
    if suffix == ".map":
        return scan_sourcemap(sha256, source_path, text)
    return []
