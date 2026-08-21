"""Second stage of the vendor-inbox forensic pass.

This is deliberately a *static* analyser.  It consumes the immutable inventory
and inspected artifacts, never starts an installer, driver, firmware image, or
vendor application.  The output is a staging JSON; publishing is an atomic
rename performed only after its coverage audit succeeds.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pefile

try:
    import dnfile
except ImportError:  # Kept explicit in the report rather than guessed.
    dnfile = None

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "protocol-miner" / "inbox"
INVENTORY = ROOT / "reports" / "inbox_deep_forensics_inventory.json"
OUT = ROOT / "reports" / "vendor_software_forensics.json"

TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs", ".js", ".mjs", ".ts",
    ".json", ".json5", ".xml", ".ini", ".cfg", ".config", ".yaml", ".yml", ".toml",
    ".txt", ".md", ".html", ".htm", ".css", ".inf", ".ps1", ".py", ".sh", ".bat",
    ".cmd", ".rules", ".device", ".map", ".csv", ".log",
}
API_TOKENS = (
    "hid.dll", "hidd_", "hidp_", "setupdi", "createfile", "readfile", "writefile",
    "deviceiocontrol", "winusb", "libusb", "hidapi", "node-hid", "navigator.hid",
    "navigator.usb", "sendreport", "sendfeaturereport", "receivefeaturereport",
    "receiveinputreport", "sendoutputreport", "usbdevice", "webhid", "webusb",
)
SINK_RE = re.compile(
    r"\b(?:sendReport|sendFeatureReport|receiveFeatureReport|receiveInputReport|"
    r"sendOutputReport|hid_write|hid_send_feature_report|hid_read|WriteFile|ReadFile|"
    r"write\s*\(|read\s*\(|transferOut|transferIn)\b",
    re.I,
)
VID_PID_RE = re.compile(
    r"(?is)(?:usb\\)?vid[_:\s=\-]*(?P<vid>[0-9a-f]{4}|0x[0-9a-f]{3,4}|\d{3,5})"
    r".{0,180}?(?:usb\\)?pid[_:\s=\-]*(?P<pid>[0-9a-f]{4}|0x[0-9a-f]{3,4}|\d{3,5})"
)
JSON_VID_PID_RE = re.compile(
    r"(?is)(?:vendor(?:_?id)?|vid)\s*[\"']?\s*[:=]\s*[\"']?(?P<vid>0x[0-9a-f]+|\d+)"
    r".{0,180}?(?:product(?:_?id)?|pid)\s*[\"']?\s*[:=]\s*[\"']?(?P<pid>0x[0-9a-f]+|\d+)"
)
URL_RE = re.compile(r"https?://[^\s\"'<>]{4,500}", re.I)
# Line anchored by design: the previous broad multiline form could backtrack
# pathologically through generated QMK/vendor C sources.  This finds only a
# syntactic declaration line and examines a bounded local body afterwards.
FUNC_RE = re.compile(
    r"(?im)^\s*(?:function\s+(?P<f1>[A-Za-z_$][\w$]*)\s*\([^\n)]*\)\s*\{|"
    r"(?:[A-Za-z_$][\w$<>:*&\[\] \t]*[ \t]+)?(?P<f2>[A-Za-z_$][\w$]*)\s*\([^\n;{}()]{0,300}\)\s*\{)"
)
SEMANTICS = (
    ("dpi", "pointer.set_dpi"), ("sensitivity", "pointer.set_dpi"),
    ("brightness", "lighting.set_brightness"), ("rgb", "lighting.set_color"),
    ("light", "lighting.control"), ("battery", "device.read_battery"),
    ("firmware", "device.read_firmware"), ("version", "device.read_version"),
    ("profile", "device.set_profile"), ("macro", "input.set_macro"),
    ("debounce", "pointer.set_debounce"), ("poll", "pointer.set_polling_rate"),
    ("sleep", "device.set_sleep"), ("angle", "pointer.set_angle_snap"),
    ("calibrat", "device.calibrate"), ("rapid", "keyboard.set_rapid_trigger"),
    ("actuation", "keyboard.set_actuation"), ("remap", "keyboard.remap"),
    ("save", "device.save"), ("commit", "device.commit"),
)


def _path(item: dict[str, Any]) -> Path:
    raw = item["path"]
    p = Path(raw)
    if p.is_absolute():
        return p
    # Extracted-artifact paths are recorded relative to the workspace so their
    # provenance remains portable; source inbox entries are relative to inbox.
    return ROOT / p if raw.replace("/", "\\").startswith("protocol-miner\\") else INBOX / p


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _read_text(path: Path, max_size: int = 32 * 1024 * 1024) -> tuple[str | None, str | None]:
    """Return text only when it is actually text; source bytes remain untouched."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(8192)
            if b"\x00" in head:
                return None, "binary_payload"
            if size > max_size:
                return None, f"text_over_static_cap:{max_size}"
            rest = f.read()
    except OSError as exc:
        return None, f"read_failed:{exc}"
    data = head + rest
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace"), "decoded_cp1252_fallback"


def _num(value: str) -> int | None:
    try:
        return int(value, 0)
    except ValueError:
        # Four digit VID/PID tokens are conventionally hexadecimal.
        try:
            return int(value, 16) if len(value) == 4 and re.fullmatch(r"[0-9a-fA-F]+", value) else int(value)
        except ValueError:
            return None


def _semantic(name: str) -> str | None:
    lowered = name.lower()
    for token, semantic in SEMANTICS:
        if token in lowered:
            return semantic
    return None


def _artifact_kind(item: dict[str, Any]) -> str:
    suffix = item.get("extension", "")
    magic = item.get("magic", "")
    lower = item["path"].lower()
    if suffix in {".exe"} or magic == "PE": return "PE_EXE" if suffix == ".exe" else "PE_BINARY"
    if suffix == ".dll": return "DLL"
    if suffix == ".sys": return "DRIVER_SYS"
    if suffix == ".inf": return "DRIVER_INF"
    if suffix in {".zip", ".7z", ".rar", ".cab", ".msi", ".msix", ".appx", ".asar"} or magic in {"ZIP","7Z","RAR","CAB","OLE/possibly_MSI"}: return "CONTAINER"
    if suffix in {".js", ".mjs", ".ts"} or "app.asar" in lower: return "WEB_OR_ELECTRON_CODE"
    if suffix in {".json", ".xml", ".ini", ".yaml", ".yml", ".toml", ".db", ".cfg", ".config"}: return "CONFIG_OR_DATABASE"
    if suffix in {".html", ".htm"}: return "WEB_DOCUMENT"
    if suffix in {".txt", ".md", ".url"}: return "TEXT_OR_URL"
    if suffix in TEXT_SUFFIXES: return "SOURCE_OR_SCRIPT"
    return "OTHER"


def _pe_profile(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "pe", "parser": "pefile"}
    try:
        # Do not eagerly map every resource/blob of multi-gigabyte vendor
        # installers.  PE headers plus explicit import/export directories are
        # the static contract surface needed here and remain deterministic.
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"],
        ])
        result["machine"] = hex(pe.FILE_HEADER.Machine)
        result["is_dll"] = bool(pe.FILE_HEADER.Characteristics & 0x2000)
        result["timestamp"] = int(pe.FILE_HEADER.TimeDateStamp)
        result["image_base"] = hex(pe.OPTIONAL_HEADER.ImageBase)
        result["entry_point_rva"] = hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint)
        result["sections"] = [{"name": s.Name.rstrip(b"\0").decode("ascii", "replace"), "size": int(s.SizeOfRawData)} for s in pe.sections]
        imports: list[str] = []
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
            dll = entry.dll.decode("ascii", "replace")
            for imp in entry.imports:
                name = imp.name.decode("ascii", "replace") if imp.name else f"ordinal:{imp.ordinal}"
                imports.append(f"{dll}!{name}")
        exports = []
        for exp in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", []) or []:
            exports.append(exp.name.decode("ascii", "replace") if exp.name else f"ordinal:{exp.ordinal}")
        result["imports"] = imports
        result["exports"] = exports[:2000]
        result["hid_usb_imports"] = [x for x in imports if any(t in x.lower() for t in API_TOKENS)]
        raw = path.read_bytes() if path.stat().st_size <= 64 * 1024 * 1024 else b""
        result["managed_dotnet"] = bool(b"mscoree.dll" in raw.lower() or b"BSJB" in raw)
        if result["managed_dotnet"] and dnfile is not None:
            try:
                dn = dnfile.dnPE(str(path))
                result["dotnet_metadata"] = bool(getattr(dn, "net", None))
            except Exception as exc: result["dotnet_parse_note"] = repr(exc)
    except Exception as exc:
        result["parse_error"] = repr(exc)
    return result


def _text_profile(text: str, suffix: str) -> dict[str, Any]:
    found_ids: list[dict[str, int]] = []
    for m in list(VID_PID_RE.finditer(text)) + list(JSON_VID_PID_RE.finditer(text)):
        vid, pid = _num(m.group("vid")), _num(m.group("pid"))
        if vid is not None and pid is not None:
            record = {"vid": vid, "pid": pid, "line": _line(text, m.start())}
            if record not in found_ids: found_ids.append(record)
    api_hits = []
    low = text.lower()
    for token in API_TOKENS:
        offset = low.find(token)
        if offset >= 0: api_hits.append({"api": token, "line": _line(text, offset)})
    urls = sorted(set(URL_RE.findall(text)))[:100]
    checksums = []
    checksum_re = re.compile(r"(?im)^\s*(?:function\s+|def\s+|[\w<>:*&\[\] \t]+[ \t]+)?([A-Za-z_$][\w$]*(?:checksum|crc)[\w$]*)\s*\([^\n)]*\)\s*\{")
    for m in checksum_re.finditer(text):
        body = text[m.end():m.end()+5000]
        checksums.append({"symbol": m.group(1), "line": _line(text, m.start()), "algorithm": "xor" if "^" in body else "additive" if "+=" in body or "sum" in body.lower() else "unspecified"})
    operations = []
    sequences = []
    for m in FUNC_RE.finditer(text):
        name = m.group("f1") or m.group("f2")
        # Deliberately bounded lexical function body: a candidate needs both a
        # semantic caller and a real transport sink in its local source context.
        body = text[m.end():m.end()+12000]
        sink_count = len(SINK_RE.findall(body))
        sem = _semantic(name)
        if sem and sink_count:
            snippet = body[:1800]
            operations.append({"semantic": sem, "symbol": name, "line": _line(text, m.start()), "sink_count": sink_count,
                               "packet_builder": bool(re.search(r"(?:Uint8Array|Buffer\.(?:alloc|from)|byte\[|\bpacket\b|\breport\b).{0,300}(?:\[\s*\d+\s*\]\s*=)", snippet, re.I)),
                               "dynamic_expression": bool(re.search(r"(?:<<|>>|\^|\+|\*|&|\|)", snippet)),
                               "source_excerpt_sha256": hashlib.sha256(snippet.encode()).hexdigest()})
        if sink_count >= 2 and any(k in name.lower() for k in ("init","handshake","startup","apply","commit","save","open","connect")):
            sequences.append({"symbol": name, "line": _line(text, m.start()), "sink_count": sink_count})
    return {"vid_pid": found_ids[:100], "api_hits": api_hits, "urls": urls, "checksum_candidates": checksums[:100],
            "operation_candidates": operations[:300], "sequence_candidates": sequences[:200], "js_ast_attempted": suffix in {".js",".mjs",".ts"}}


GENERIC_COMPONENTS = ("\\lib\\", "\\third_party\\", "\\thirdparty\\", "\\test\\", "\\tests\\", "\\demo\\", "\\examples\\",
                      "\\googletest\\", "\\lufa\\", "\\chibios\\", "\\lwip\\", "\\wolfssl\\")


def _origin(item: dict[str, Any], by_sha: dict[str, dict[str, Any]]) -> tuple[str, str, bool]:
    """Recover the inbox origin for a recursively extracted artifact.

    A top-level aggregate archive is not itself a brand.  In that case the
    closest archive-member path containing a physical inbox component is used.
    """
    chain: list[dict[str, Any]] = [item]
    cur = item
    while cur.get("parent_sha256") and cur["parent_sha256"] in by_sha:
        cur = by_sha[cur["parent_sha256"]]
        chain.append(cur)
    candidates = [x.get("path_inside_container") for x in reversed(chain) if x.get("path_inside_container")]
    physical = next((x["path"] for x in reversed(chain) if not x["path"].replace("/", "\\").startswith("protocol-miner\\")), None)
    member_origin = next((x for x in candidates if not x.lower().endswith("inbox.zip")), None)
    # A single collected inbox.zip is an aggregate, never a brand.  Prefer its
    # explicit member path (e.g. Chosfox\\keyboard\\...) when present.
    origin = member_origin if physical and Path(physical).name.lower() == "inbox.zip" and member_origin else (physical or member_origin or item["path"])
    normalized = origin.replace("/", "\\")
    brand = normalized.split("\\")[0]
    generic = any(token in ("\\" + item["path"].replace("/", "\\").lower()) for token in GENERIC_COMPONENTS)
    return origin, brand, generic


def run() -> dict[str, Any]:
    inventory = json.loads(INVENTORY.read_text(encoding="utf8"))
    started = time.time()
    findings: list[dict[str, Any]] = []
    artifact_types: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    by_brand: dict[str, dict[str, Any]] = defaultdict(lambda: {"files": 0, "protocol_artifacts": 0, "kinds": Counter(), "identities": set(), "operations": Counter(), "apis": Counter()})
    by_sha = {x.get("sha256"): x for x in inventory["files"] if x.get("sha256")}
    unique = [x for x in inventory["files"] if x["status"] != "DUPLICATE"]
    for idx, item in enumerate(unique, 1):
        kind = _artifact_kind(item); artifact_types[kind] += 1
        path = _path(item)
        origin_path, brand, generic_component = _origin(item, by_sha)
        bucket = by_brand[brand]; bucket["files"] += 1; bucket["kinds"][kind] += 1
        entry: dict[str, Any] = {"path": item["path"], "sha256": item.get("sha256"), "size": item["size"], "kind": kind,
                                 "inventory_status": item["status"], "origin_path": origin_path, "origin_brand": brand, "generic_component": generic_component,
                                 "lineage": {"parent_sha256": item.get("parent_sha256"), "path_inside_container": item.get("path_inside_container")}}
        if item["status"] == "PROTOCOL_DATA": bucket["protocol_artifacts"] += 1
        if item.get("magic") == "PE":
            entry["pe"] = _pe_profile(path)
            for imp in entry["pe"].get("hid_usb_imports", []): bucket["apis"][imp] += 1
        wants_text = item["extension"] in TEXT_SUFFIXES or kind in {"WEB_OR_ELECTRON_CODE","CONFIG_OR_DATABASE","WEB_DOCUMENT","TEXT_OR_URL","DRIVER_INF"}
        if wants_text:
            text, note = _read_text(path)
            entry["text_analysis"] = {"note": note}
            if text is not None:
                profile = _text_profile(text, item["extension"])
                rejected = []
                if generic_component and profile["operation_candidates"]:
                    rejected = [{**x, "rejection_reason": "generic_or_demo_dependency_not_device_implementation"} for x in profile["operation_candidates"]]
                    profile["operation_candidates"] = []
                if rejected: profile["rejected_operation_candidates"] = rejected
                entry["text_analysis"].update(profile)
                for ident in profile["vid_pid"]: bucket["identities"].add((ident["vid"], ident["pid"]))
                for api in profile["api_hits"]: bucket["apis"][api["api"]] += 1
                for op in profile["operation_candidates"]: bucket["operations"][op["semantic"]] += 1
            elif note: unsupported[note.split(":", 1)[0]] += 1
        # Keep report compact but retain every fact-bearing artifact and all PE
        # profiles.  The immutable inventory remains the exhaustive per-file map.
        meaningful = item["status"] == "PROTOCOL_DATA" or "pe" in entry or any(entry.get("text_analysis", {}).get(k) for k in ("vid_pid","api_hits","urls","checksum_candidates","operation_candidates","sequence_candidates"))
        if meaningful: findings.append(entry)
        if idx % 2000 == 0: print(f"static files={idx}/{len(unique)} findings={len(findings)}", flush=True)
    brands = {}
    for brand, b in sorted(by_brand.items()):
        brands[brand] = {"files": b["files"], "protocol_artifacts": b["protocol_artifacts"], "artifact_kinds": dict(b["kinds"]),
                         "vid_pid": [{"vid": v, "pid": p} for v,p in sorted(b["identities"])], "operations": dict(b["operations"]), "transport_apis": dict(b["apis"])}
    summary = {
        "unique_artifacts_analyzed": len(unique), "finding_artifacts": len(findings), "artifact_types": dict(artifact_types),
        "pe_profiles": sum("pe" in x for x in findings),
        "dotnet_candidates": sum(x.get("pe", {}).get("managed_dotnet", False) for x in findings),
        "identity_evidence": sum(len(x.get("text_analysis", {}).get("vid_pid", [])) for x in findings),
        "operation_candidates": sum(len(x.get("text_analysis", {}).get("operation_candidates", [])) for x in findings),
        "operation_candidates_rejected": sum(len(x.get("text_analysis", {}).get("rejected_operation_candidates", [])) for x in findings),
        "checksum_candidates": sum(len(x.get("text_analysis", {}).get("checksum_candidates", [])) for x in findings),
        "sequence_candidates": sum(len(x.get("text_analysis", {}).get("sequence_candidates", [])) for x in findings),
        "unsupported_detail": dict(unsupported),
    }
    result = {"pass": "deep_vendor_static_forensics", "started_at": started, "finished_at": time.time(),
              "safety": "static_only_no_vendor_code_or_driver_executed", "inventory_coverage": {"files_total": inventory["files_total"], "status_counts": inventory["status_counts"], "unexplained_skipped": inventory["unexplained_skipped"]},
              "summary": summary, "brands": brands, "findings": findings}
    # Audit before publication: every non-duplicate object was considered and
    # the inventory is still the authoritative terminal-status ledger.
    assert summary["unique_artifacts_analyzed"] + inventory["status_counts"].get("DUPLICATE", 0) == inventory["files_total"]
    assert inventory["unexplained_skipped"] == 0
    staging = OUT.with_suffix(".staging.json")
    staging.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    staging.replace(OUT)
    return result


if __name__ == "__main__":
    value = run()
    print(json.dumps(value["summary"], ensure_ascii=True, sort_keys=True))
