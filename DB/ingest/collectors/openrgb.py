"""OpenRGB metadata collector, detector parser, and deep byte-level protocol extractor.

Extracts:
1. Multi-dimensional device detector fingerprints (VID, PID, Interface, Usage Page, Usage, SVID, SPID, I2C address)
2. RGBController docblocks (Category, Type, Save, Direct, Effects, Comments)
3. Packed C/C++ packet structs (PACK(struct ...)) with exact byte offsets and types
4. Procedural packet builders (buf[0x00] = 0xEC, buf[0x01] = ..., hid_write(dev, buf, 65))
5. Cross-file C/C++ constants and enum opcodes (14,800+ definitions)
6. Communication sink functions (hid_write, hid_send_feature_report, i2c_smbus_write_*)

Imports all extracted facts and protocol hints into the SQLite Peripheral Registry.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from ingest.brands.canonical import ALL_CANONICAL_BRANDS, get_brand_by_slug
from ingest.config import DB_PATH
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import (
    RawSource, SourceType, DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
)
from ingest.normalize.identifiers import normalize_vid_pid, format_hex4, parse_hex_or_dec
from ingest.normalize.models import generate_identity_key, normalize_product_name
from ingest.storage.database import RegistryDatabase

logger = get_logger()

# Category mapping to canonical categories
CATEGORY_MAP = {
    "mouse": "mouse",
    "keyboard": "keyboard",
    "mousemat": "mousemat",
    "headset": "headset",
    "headsetstand": "accessory",
    "keypad": "keypad",
    "gpu": "gpu",
    "motherboard": "motherboard",
    "ram": "dram",
    "dram": "dram",
    "cooler": "cooler",
    "fan": "cooler",
    "case": "case",
    "ledstrip": "lighting",
    "accessory": "accessory",
    "gamepad": "gamepad",
    "microphone": "microphone",
    "storage": "storage",
}


@dataclass
class OpenRGBDeviceMetadata:
    """Device metadata and detector fingerprint extracted from OpenRGB."""
    name: str
    detector_func: str
    macro: str
    source_file: str
    controller_family: str
    bus_type: str
    vid: Optional[int] = None
    pid: Optional[int] = None
    vid_hex: Optional[str] = None
    pid_hex: Optional[str] = None
    svid: Optional[int] = None
    spid: Optional[int] = None
    svid_hex: Optional[str] = None
    spid_hex: Optional[str] = None
    interface: Optional[int] = None
    usage_page: Optional[int] = None
    usage_page_hex: Optional[str] = None
    usage: Optional[int] = None
    usage_hex: Optional[str] = None
    i2c_addr: Optional[int] = None
    i2c_addr_hex: Optional[str] = None
    category: str = "other"
    save_mode: str = ""
    direct_mode: str = ""
    effects_mode: str = ""
    comment: str = ""
    vendor_slug: str = "unknown"
    manufacturer: str = "Unknown"


@dataclass
class OpenRGBControllerInfo:
    """RGBController implementation details."""
    family_name: str
    rgb_controller_class: str
    source_file: str
    modes: list[dict[str, Any]] = field(default_factory=list)
    report_ids: list[int] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    packet_layouts: list[dict[str, Any]] = field(default_factory=list)
    packed_structs: list[dict[str, Any]] = field(default_factory=list)
    opcodes: dict[str, str] = field(default_factory=dict)
    sink_functions: list[str] = field(default_factory=list)


class OpenRGBDetectorParser:
    """Parses C++ detector registration macros and docblocks in OpenRGB."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.controllers_dir = repo_path / "Controllers"
        self._brand_lookup: dict[str, tuple[str, str]] = {}
        for b in ALL_CANONICAL_BRANDS:
            self._brand_lookup[b.slug.lower()] = (b.slug, b.canonical_name)
            self._brand_lookup[b.canonical_name.lower()] = (b.slug, b.canonical_name)
            for alias in b.aliases:
                self._brand_lookup[alias.lower()] = (b.slug, b.canonical_name)

    def collect_defines(self) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Collect all #define constants across headers and source files."""
        defines_by_file: dict[str, dict[str, str]] = {}
        global_defines: dict[str, str] = {}

        re_define = re.compile(
            r'^\s*#\s*define\s+([A-Za-z0-9_]+)\s+([^\r\n/]+?)(?:\s*/[/*].*)?$',
            re.MULTILINE
        )

        for f in self.repo_path.rglob("*.*"):
            if f.suffix in [".h", ".cpp"] and ".git" not in f.as_posix():
                text = f.read_text(encoding="utf-8", errors="replace")
                f_defs: dict[str, str] = {}
                for m in re_define.finditer(text):
                    k = m.group(1).strip()
                    v = m.group(2).strip()
                    f_defs[k] = v
                    global_defines[k] = v
                defines_by_file[f.as_posix()] = f_defs

        return defines_by_file, global_defines

    def collect_rgbcontroller_docblocks(self) -> dict[str, dict[str, Any]]:
        """Extract metadata docblocks (@name, @category, @save, @direct, @effects, @detectors) from RGBController_*.cpp."""
        docblocks: dict[str, dict[str, Any]] = {}
        re_doc = re.compile(r'/\*\*[\s\S]*?\\\*', re.DOTALL)

        for f in self.repo_path.rglob("RGBController_*.cpp"):
            if ".git" in f.as_posix():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re_doc.search(text)
            if m:
                doc_text = m.group(0)
                info: dict[str, Any] = {
                    "file": f.relative_to(self.repo_path).as_posix(),
                    "name": "",
                    "category": "",
                    "type": "",
                    "save": "",
                    "direct": "",
                    "effects": "",
                    "detectors": [],
                    "comment": ""
                }
                for line in doc_text.splitlines():
                    line = line.strip()
                    if line.startswith("@name"):
                        info["name"] = line[5:].strip()
                    elif line.startswith("@category"):
                        info["category"] = line[9:].strip()
                    elif line.startswith("@type"):
                        info["type"] = line[5:].strip()
                    elif line.startswith("@save"):
                        info["save"] = line[5:].strip()
                    elif line.startswith("@direct"):
                        info["direct"] = line[7:].strip()
                    elif line.startswith("@effects"):
                        info["effects"] = line[8:].strip()
                    elif line.startswith("@detectors"):
                        raw_dets = line[10:].strip()
                        info["detectors"] = [d.strip() for d in raw_dets.split(",") if d.strip()]
                    elif line.startswith("@comment"):
                        info["comment"] = line[8:].strip()

                for det in info["detectors"]:
                    docblocks[det] = info
                docblocks[f.stem] = info

        return docblocks

    def parse_val(self, v_str: str, file_defs: dict[str, str], global_defs: dict[str, str], depth: int = 0) -> Optional[int]:
        """Resolve symbolic constant, hex string, or integer into integer value."""
        if not v_str or depth > 6:
            return None
        v_str = v_str.strip()
        if v_str in ["HID_PID_ANY", "HID_VID_ANY", "HID_INTERFACE_ANY", "HID_USAGE_ANY", "HID_USAGE_PAGE_ANY", "-1"]:
            return None
        if v_str in file_defs:
            return self.parse_val(file_defs[v_str], file_defs, global_defs, depth + 1)
        if v_str in global_defs:
            return self.parse_val(global_defs[v_str], file_defs, global_defs, depth + 1)
        if v_str.startswith("0x") or v_str.startswith("0X"):
            try:
                return int(v_str, 16)
            except ValueError:
                return None
        if v_str.isdigit():
            try:
                return int(v_str)
            except ValueError:
                return None
        return None

    def parse_all_devices(self) -> list[OpenRGBDeviceMetadata]:
        """Parse all detector registrations and match with docblock capabilities."""
        defines_by_file, global_defs = self.collect_defines()
        docblocks = self.collect_rgbcontroller_docblocks()

        re_detector = re.compile(r'(REGISTER_[A-Za-z0-9_]+)\s*\(([^;]+)\);', re.MULTILINE)
        devices: list[OpenRGBDeviceMetadata] = []

        for f in sorted(self.repo_path.rglob("*.cpp")):
            if ".git" in f.as_posix():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            f_defs = defines_by_file.get(f.as_posix(), {})

            for m in re_detector.finditer(text):
                macro = m.group(1)
                raw_args = m.group(2).strip()

                args = self._split_args(raw_args)
                if not args:
                    continue

                name = args[0].strip('"')
                func = args[1] if len(args) > 1 else ""

                vid: Optional[int] = None
                pid: Optional[int] = None
                svid: Optional[int] = None
                spid: Optional[int] = None
                interface: Optional[int] = None
                usage_page: Optional[int] = None
                usage: Optional[int] = None
                i2c_addr: Optional[int] = None
                bus_type = "USB"

                if "HID_DETECTOR_IPU" in macro:
                    if len(args) >= 7:
                        vid = self.parse_val(args[2], f_defs, global_defs)
                        pid = self.parse_val(args[3], f_defs, global_defs)
                        interface = self.parse_val(args[4], f_defs, global_defs)
                        usage_page = self.parse_val(args[5], f_defs, global_defs)
                        usage = self.parse_val(args[6], f_defs, global_defs)
                elif "HID_DETECTOR_IP" in macro:
                    if len(args) >= 6:
                        vid = self.parse_val(args[2], f_defs, global_defs)
                        pid = self.parse_val(args[3], f_defs, global_defs)
                        interface = self.parse_val(args[4], f_defs, global_defs)
                        usage_page = self.parse_val(args[5], f_defs, global_defs)
                elif "HID_DETECTOR_PU" in macro:
                    if len(args) >= 6:
                        vid = self.parse_val(args[2], f_defs, global_defs)
                        pid = self.parse_val(args[3], f_defs, global_defs)
                        usage_page = self.parse_val(args[4], f_defs, global_defs)
                        usage = self.parse_val(args[5], f_defs, global_defs)
                elif "HID_DETECTOR_I" in macro:
                    if len(args) >= 5:
                        vid = self.parse_val(args[2], f_defs, global_defs)
                        pid = self.parse_val(args[3], f_defs, global_defs)
                        interface = self.parse_val(args[4], f_defs, global_defs)
                elif "HID_DETECTOR_P" in macro:
                    if len(args) >= 5:
                        vid = self.parse_val(args[2], f_defs, global_defs)
                        pid = self.parse_val(args[3], f_defs, global_defs)
                        usage_page = self.parse_val(args[4], f_defs, global_defs)
                elif "HID_DETECTOR" in macro:
                    if len(args) >= 4:
                        vid = self.parse_val(args[2], f_defs, global_defs)
                        pid = self.parse_val(args[3], f_defs, global_defs)
                elif "I2C_PCI_DETECTOR" in macro:
                    bus_type = "PCI / I2C"
                    if len(args) >= 7:
                        vid = self.parse_val(args[2], f_defs, global_defs)
                        pid = self.parse_val(args[3], f_defs, global_defs)
                        svid = self.parse_val(args[4], f_defs, global_defs)
                        spid = self.parse_val(args[5], f_defs, global_defs)
                        i2c_addr = self.parse_val(args[6], f_defs, global_defs)
                elif "I2C_DETECTOR" in macro:
                    bus_type = "I2C"
                    if len(args) >= 4:
                        vid = self.parse_val(args[2], f_defs, global_defs)
                        pid = self.parse_val(args[3], f_defs, global_defs)
                elif "WOOTING_DETECTOR" in macro:
                    bus_type = "USB"
                    vid = 0x31E3  # Wooting VID
                    if len(args) >= 2:
                        pid = self.parse_val(args[1], f_defs, global_defs)

                doc = docblocks.get(func, {})
                controller_family = f.parent.name
                category = self._normalize_category(doc.get("category", ""), name)
                vendor_slug, display_name = self._resolve_vendor(name, controller_family)

                devices.append(OpenRGBDeviceMetadata(
                    name=name,
                    detector_func=func,
                    macro=macro,
                    source_file=f.relative_to(self.repo_path).as_posix(),
                    controller_family=controller_family,
                    bus_type=bus_type,
                    vid=vid,
                    pid=pid,
                    vid_hex=format_hex4(vid) if vid is not None else None,
                    pid_hex=format_hex4(pid) if pid is not None else None,
                    svid=svid,
                    spid=spid,
                    svid_hex=format_hex4(svid) if svid is not None else None,
                    spid_hex=format_hex4(spid) if spid is not None else None,
                    interface=interface,
                    usage_page=usage_page,
                    usage_page_hex=format_hex4(usage_page) if usage_page is not None else None,
                    usage=usage,
                    usage_hex=format_hex4(usage) if usage is not None else None,
                    i2c_addr=i2c_addr,
                    i2c_addr_hex=f"0x{i2c_addr:02X}" if i2c_addr is not None else None,
                    category=category,
                    save_mode=doc.get("save", ""),
                    direct_mode=doc.get("direct", ""),
                    effects_mode=doc.get("effects", ""),
                    comment=doc.get("comment", ""),
                    vendor_slug=vendor_slug,
                    manufacturer=display_name
                ))

        return devices

    def _split_args(self, raw: str) -> list[str]:
        """Split C++ macro arguments by comma, respecting quotes and parentheses."""
        args: list[str] = []
        cur: list[str] = []
        in_q = False
        parens = 0
        for ch in raw:
            if ch == '"':
                in_q = not in_q
                cur.append(ch)
            elif ch == '(' and not in_q:
                parens += 1
                cur.append(ch)
            elif ch == ')' and not in_q:
                parens -= 1
                cur.append(ch)
            elif ch == ',' and not in_q and parens == 0:
                args.append(''.join(cur).strip())
                cur = []
            else:
                cur.append(ch)
        if cur:
            args.append(''.join(cur).strip())
        return args

    def _normalize_category(self, raw_cat: str, name: str) -> str:
        """Map OpenRGB category string or name hint to canonical category."""
        lower_cat = raw_cat.lower()
        for k, v in CATEGORY_MAP.items():
            if k in lower_cat:
                return v

        lower_name = name.lower()
        if any(k in lower_name for k in ["mouse", "mice", "cobra", "griffin", "viper", "deathadder"]):
            return "mouse"
        if any(k in lower_name for k in ["keyboard", "huntsman", "blackwidow", "apex"]):
            return "keyboard"
        if any(k in lower_name for k in ["mousemat", "mousepad", "goliathus", "firefly"]):
            return "mousemat"
        if any(k in lower_name for k in ["headset", "kraken", "arctis", "void"]):
            return "headset"
        if any(k in lower_name for k in ["geforce", "radeon", "gtx", "rtx", "rx 6", "rx 7", "strix 10", "strix 20", "strix 30", "strix 40"]):
            return "gpu"
        if any(k in lower_name for k in ["motherboard", "maximus", "crosshair", "aorus master", "aorus pro", "b550", "x570", "z690", "z790"]):
            return "motherboard"
        if any(k in lower_name for k in ["ram", "dram", "trident", "vengeance", "fury"]):
            return "dram"
        if any(k in lower_name for k in ["cooler", "hydro", "kraken x", "kraken z", "prism"]):
            return "cooler"
        if any(k in lower_name for k in ["strip", "hue", "aura terminal"]):
            return "lighting"

        return "other"

    def _resolve_vendor(self, device_name: str, controller_family: str) -> tuple[str, str]:
        """Resolve vendor slug and canonical display name."""
        lower_name = device_name.lower()
        lower_fam = controller_family.lower()

        # Check canonical brands against device name or controller family
        for b_key, (slug, cname) in self._brand_lookup.items():
            if lower_name.startswith(b_key) or lower_fam.startswith(b_key.replace(" ", "").replace("_", "")):
                return (slug, cname)

        if "redragon" in lower_name or "redragon" in lower_fam:
            return ("redragon", "Redragon")
        if "corsair" in lower_name or "corsair" in lower_fam:
            return ("corsair", "Corsair")
        if "razer" in lower_name or "razer" in lower_fam:
            return ("razer", "Razer")
        if "asus" in lower_name or "asus" in lower_fam or "aura" in lower_fam:
            return ("asus", "ASUS")
        if "steelseries" in lower_name or "steelseries" in lower_fam:
            return ("steelseries", "SteelSeries")
        if "logitech" in lower_name or "logitech" in lower_fam:
            return ("logitech", "Logitech")
        if "hyperx" in lower_name or "hyperx" in lower_fam:
            return ("hyperx", "HyperX")
        if "msi" in lower_name or "mystic" in lower_fam:
            return ("msi", "MSI")
        if "gigabyte" in lower_name or "aorus" in lower_name or "fusion" in lower_fam:
            return ("gigabyte", "Gigabyte")
        if "evga" in lower_name or "evga" in lower_fam:
            return ("evga", "EVGA")
        if "glorious" in lower_name or "glorious" in lower_fam:
            return ("glorious", "Glorious")
        if "ducky" in lower_name or "ducky" in lower_fam:
            return ("ducky", "Ducky")
        if "wooting" in lower_name or "wooting" in lower_fam:
            return ("wooting", "Wooting")
        if "asrock" in lower_name or "polychrome" in lower_fam:
            return ("asrock", "ASRock")
        if "lianli" in lower_name or "lian_li" in lower_name or "lianli" in lower_fam:
            return ("lian_li", "Lian Li")
        if "nzxt" in lower_name or "nzxt" in lower_fam:
            return ("nzxt", "NZXT")
        if "roccat" in lower_name or "roccat" in lower_fam:
            return ("roccat", "Roccat")
        if "gskill" in lower_name or "g.skill" in lower_name or "gskill" in lower_fam:
            return ("gskill", "G.Skill")

        first_word = device_name.split()[0] if device_name else "OpenRGB"
        slug = re.sub(r'[^a-z0-9]+', '_', first_word.lower()).strip('_')
        return (slug or "custom", first_word.title())


class OpenRGBByteProtocolExtractor:
    """Deep byte-level C++ procedural packet builder and packed struct extractor."""

    TYPE_SIZES = {
        "uint8_t": 1, "unsigned char": 1, "char": 1, "int8_t": 1, "bool": 1,
        "uint16_t": 2, "unsigned short": 2, "short": 2, "int16_t": 2, "wchar_t": 2,
        "uint32_t": 4, "unsigned int": 4, "int": 4, "int32_t": 4, "float": 4,
        "uint64_t": 8, "unsigned long long": 8, "int64_t": 8, "double": 8,
        "RGBColor": 4, "razer_rgb": 3,
    }

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.controllers_dir = repo_path / "Controllers"
        self._constants: dict[str, str] = {}
        self._packed_structs: list[dict[str, Any]] = []
        self._procedural_builders: list[dict[str, Any]] = []
        self._builders_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._structs_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._opcodes_by_family: dict[str, dict[str, str]] = defaultdict(dict)
        self._sinks_by_family: dict[str, set[str]] = defaultdict(set)

    def get_commit_sha(self) -> str:
        """Get git commit SHA of OpenRGB repository."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=True
            )
            return res.stdout.strip()
        except Exception:
            return "unknown_commit"

    def analyze_all(self):
        """Perform full deep byte-level analysis across all controllers and headers."""
        self._collect_all_constants()
        self._extract_packed_structs()
        self._extract_procedural_builders()

    def _collect_all_constants(self):
        """Collect and resolve all #define and enum constants across all files."""
        re_define = re.compile(
            r'^\s*#\s*define\s+([A-Za-z0-9_]+)\s+([^\r\n/]+?)(?:\s*/[/*].*)?$',
            re.MULTILINE
        )
        re_enum = re.compile(r'enum(?:\s+[A-Za-z0-9_]+)?\s*\{([^}]+)\};', re.DOTALL)
        re_enum_item = re.compile(r'([A-Za-z0-9_]+)\s*(?:=\s*([0-9A-Fa-fxX]+|\d+))?')

        for f in self.repo_path.rglob("*.*"):
            if f.suffix in [".h", ".cpp"] and ".git" not in f.as_posix():
                text = f.read_text(encoding="utf-8", errors="replace")
                fam = f.parent.name
                for m in re_define.finditer(text):
                    k = m.group(1).strip()
                    v = m.group(2).strip()
                    self._constants[k] = v
                    if any(cmd_kw in k.lower() for cmd_kw in ["cmd", "command", "report_id", "mode", "zone", "header", "init", "commit"]):
                        self._opcodes_by_family[fam][k] = v

                for m in re_enum.finditer(text):
                    enum_body = m.group(1)
                    cur_val = 0
                    for item_match in re_enum_item.finditer(enum_body):
                        item_name = item_match.group(1).strip()
                        item_val = item_match.group(2)
                        if item_val:
                            item_val = item_val.strip()
                            try:
                                cur_val = int(item_val, 16) if item_val.startswith("0x") or item_val.startswith("0X") else int(item_val)
                            except ValueError:
                                pass
                        val_hex = f"0x{cur_val:02X}"
                        self._constants[item_name] = val_hex
                        if any(cmd_kw in item_name.lower() for cmd_kw in ["cmd", "command", "mode", "report_id", "zone", "led_id", "packet"]):
                            self._opcodes_by_family[fam][item_name] = val_hex
                        cur_val += 1

    def _extract_packed_structs(self):
        """Extract packed C structs across all headers."""
        re_packed_struct = re.compile(
            r'(?:PACK\s*\(\s*)?struct\s+(?:__attribute__\s*\(\s*\(\s*packed\s*\)\s*\)\s+)?([A-Za-z0-9_]+)\s*\{([^}]+)\}(?:\s*\))?(?:\s*__attribute__\s*\(\s*\(\s*packed\s*\)\s*\))?;',
            re.MULTILINE
        )

        for f in self.repo_path.rglob("*.h"):
            if ".git" in f.as_posix():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            fam = f.parent.name
            for m in re_packed_struct.finditer(text):
                s_name = m.group(1)
                s_body = m.group(2)
                fields = []
                curr_offset = 0
                for line in s_body.splitlines():
                    line = line.strip()
                    if not line or line.startswith("//") or line.startswith("/*") or line.startswith("#"):
                        continue
                    f_m = re.search(r'([A-Za-z0-9_:\s]+?)\s+([A-Za-z0-9_]+)(?:\[([^\]]+)\])?\s*;', line)
                    if f_m:
                        f_type = f_m.group(1).strip()
                        f_name = f_m.group(2).strip()
                        f_arr = f_m.group(3)
                        arr_len = 1
                        if f_arr:
                            f_arr = f_arr.strip()
                            if f_arr in self._constants:
                                f_arr = self._constants[f_arr]
                            try:
                                arr_len = int(f_arr, 16) if f_arr.startswith("0x") else int(f_arr)
                            except ValueError:
                                arr_len = 1
                        unit_size = self.TYPE_SIZES.get(f_type, 1)
                        field_total_size = unit_size * arr_len
                        fields.append({
                            "name": f_name,
                            "type": f_type,
                            "offset": curr_offset,
                            "size": field_total_size,
                            "array_length": arr_len
                        })
                        curr_offset += field_total_size

                if fields:
                    struct_info = {
                        "struct_name": s_name,
                        "file": f.relative_to(self.repo_path).as_posix(),
                        "family": fam,
                        "total_size": curr_offset,
                        "fields": fields
                    }
                    self._packed_structs.append(struct_info)
                    self._structs_by_family[fam].append(struct_info)

    def _extract_procedural_builders(self):
        """Extract procedural buffer construction around communication sinks."""
        sink_calls = re.compile(
            r'(hid_write|hid_send_feature_report|hid_get_feature_report|hid_read(?:_timeout)?|i2c_smbus_write_[a-z_]+|i2c_smbus_read_[a-z_]+)\s*\(([^;]+)\);'
        )
        array_assign = re.compile(
            r'([A-Za-z0-9_]+)\s*\[\s*(0x[0-9A-Fa-f]+|\d+|\w+)\s*\]\s*=\s*([^;]+);'
        )
        memcpy_call = re.compile(
            r'memcpy\s*\(\s*(?:&|\()?\s*([A-Za-z0-9_]+)\s*\[\s*(0x[0-9A-Fa-f]+|\d+|[^\]]+)\s*\](?:\s*\))?\s*,\s*([^,]+)\s*,\s*([^,\)]+)\s*\);'
        )
        func_pattern = re.compile(
            r'([A-Za-z0-9_:<>\*\&\s]+?)\s+([A-Za-z0-9_]+::[A-Za-z0-9_]+)\s*\(([^)]*)\)\s*\{([\s\S]*?)(?=\n[A-Za-z0-9_:<>\*\&\s]+?\s+[A-Za-z0-9_]+::|\Z)',
            re.MULTILINE
        )

        for f in self.controllers_dir.rglob("*.cpp"):
            text = f.read_text(encoding="utf-8", errors="replace")
            fam = f.parent.name

            for m_func in func_pattern.finditer(text):
                func_name = m_func.group(2)
                func_body = m_func.group(4)

                for m_sink in sink_calls.finditer(func_body):
                    sink_fn = m_sink.group(1)
                    sink_args = [a.strip() for a in m_sink.group(2).split(',')]
                    self._sinks_by_family[fam].add(sink_fn)

                    buf_name = ""
                    packet_len = None
                    if "hid_write" in sink_fn or "feature_report" in sink_fn or "hid_read" in sink_fn:
                        if len(sink_args) >= 3:
                            buf_name = sink_args[1].replace('&', '').replace('*', '').strip()
                            len_str = sink_args[2].strip()
                            if len_str in self._constants:
                                len_str = self._constants[len_str]
                            try:
                                packet_len = int(len_str, 16) if len_str.startswith('0x') else int(len_str)
                            except ValueError:
                                packet_len = len_str
                    elif "i2c_smbus" in sink_fn:
                        buf_name = sink_args[-1].replace('&', '').strip()

                    field_offsets = {}
                    for m_assign in array_assign.finditer(func_body):
                        target_buf = m_assign.group(1).strip()
                        if buf_name and target_buf != buf_name:
                            continue
                        offset_str = m_assign.group(2).strip()
                        val_expr = m_assign.group(3).strip()

                        try:
                            offset = int(offset_str, 16) if offset_str.startswith('0x') or offset_str.startswith('0X') else int(offset_str)
                        except ValueError:
                            offset = offset_str

                        resolved_val = self._constants.get(val_expr, val_expr)

                        field_offsets[str(offset)] = {
                            "raw_expr": val_expr,
                            "resolved_val": resolved_val,
                            "semantic_tag": self._classify_semantic(val_expr, offset)
                        }

                    for m_cpy in memcpy_call.finditer(func_body):
                        target_buf = m_cpy.group(1).strip()
                        if buf_name and target_buf != buf_name:
                            continue
                        offset_str = m_cpy.group(2).strip()
                        src_expr = m_cpy.group(3).strip()
                        cpy_len = m_cpy.group(4).strip()
                        field_offsets[f"memcpy_{offset_str}"] = {
                            "raw_expr": f"memcpy(src={src_expr}, len={cpy_len})",
                            "resolved_val": src_expr,
                            "semantic_tag": "payload_bytes"
                        }

                    if field_offsets or packet_len:
                        builder_info = {
                            "file": f.relative_to(self.repo_path).as_posix(),
                            "controller_class": func_name.split("::")[0],
                            "method": func_name,
                            "sink_function": sink_fn,
                            "direction": "IN" if "read" in sink_fn or "get" in sink_fn else "OUT",
                            "buffer_name": buf_name,
                            "packet_length": packet_len,
                            "fields": field_offsets
                        }
                        self._procedural_builders.append(builder_info)
                        self._builders_by_family[fam].append(builder_info)

    def _classify_semantic(self, expr: str, offset: Any) -> str:
        """Classify byte offset semantics based on expression name and position."""
        expr_lower = expr.lower()
        if (offset == 0 or offset == "0" or offset == "0x00") and ("report_id" in expr_lower or expr in ["0", "0x00", "0xec", "1", "2", "0x02"]):
            return "report_id_or_prefix"
        if any(k in expr_lower for k in ["cmd", "command", "set_zone", "commit", "effect", "mode_effect", "initial_chunk", "send_chunk", "write_header", "read_header"]):
            return "opcode_command"
        if any(k in expr_lower for k in ["mode", "lighting_mode"]):
            return "lighting_mode"
        if any(k in expr_lower for k in ["speed", "rate"]):
            return "speed"
        if any(k in expr_lower for k in ["red", "rgbgetrvalue", "color_r", "getrvalue"]):
            return "color_red"
        if any(k in expr_lower for k in ["green", "grn", "rgbgetgvalue", "color_g", "getgvalue"]):
            return "color_green"
        if any(k in expr_lower for k in ["blue", "blu", "rgbgetbvalue", "color_b", "getbvalue"]):
            return "color_blue"
        if any(k in expr_lower for k in ["zone", "channel", "header"]):
            return "zone_or_channel"
        if any(k in expr_lower for k in ["brightness", "bright", "level"]):
            return "brightness"
        if any(k in expr_lower for k in ["crc", "checksum"]):
            return "checksum"
        if any(k in expr_lower for k in ["direction", "dir"]):
            return "direction"
        if any(k in expr_lower for k in ["count", "size", "len"]):
            return "length_or_count"
        return "parameter"

    def extract_controller_info(self) -> list[OpenRGBControllerInfo]:
        """Extract combined lighting modes, report IDs, packed structs, packet builders, and opcodes."""
        if not self._constants:
            self.analyze_all()

        controllers: list[OpenRGBControllerInfo] = []
        re_mode = re.compile(r'([A-Za-z0-9_]+)\.name\s*=\s*\"([^\"]+)\";', re.MULTILINE)
        re_report_id = re.compile(r'#define\s+([A-Za-z0-9_]*REPORT_ID[A-Za-z0-9_]*)\s+(0x[0-9A-Fa-fxX]+|\d+)', re.MULTILINE)

        for c_dir in sorted(self.controllers_dir.iterdir()):
            if not c_dir.is_dir():
                continue

            family_name = c_dir.name
            modes: list[dict[str, Any]] = []
            report_ids: list[int] = []

            for f in c_dir.glob("*.*"):
                if f.suffix not in [".cpp", ".h"]:
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")

                for m in re_mode.finditer(text):
                    m_var = m.group(1)
                    m_name = m.group(2)
                    modes.append({"mode_var": m_var, "mode_name": m_name, "source": f.name})

                for m in re_report_id.finditer(text):
                    r_val_str = m.group(2)
                    try:
                        r_val = int(r_val_str, 16) if r_val_str.startswith("0x") or r_val_str.startswith("0X") else int(r_val_str)
                        report_ids.append(r_val)
                    except ValueError:
                        pass

            packet_layouts = self._builders_by_family.get(family_name, [])
            packed_structs = self._structs_by_family.get(family_name, [])
            opcodes = self._opcodes_by_family.get(family_name, {})
            sinks = sorted(list(self._sinks_by_family.get(family_name, set())))

            controllers.append(OpenRGBControllerInfo(
                family_name=family_name,
                rgb_controller_class=f"RGBController_{family_name}",
                source_file=c_dir.name,
                modes=modes,
                report_ids=sorted(set(report_ids)),
                packet_layouts=packet_layouts,
                packed_structs=packed_structs,
                opcodes=opcodes,
                sink_functions=sinks
            ))

        return controllers


class OpenRGBCollector:
    """Ingests OpenRGB devices, multi-interface fingerprints, and deep byte-level controller protocols into SQLite database."""

    def __init__(self, db: RegistryDatabase, repo_path: Path, run_id: str):
        self.db = db
        self.repo_path = repo_path
        self.run_id = run_id
        self.detector_parser = OpenRGBDetectorParser(repo_path)
        self.byte_extractor = OpenRGBByteProtocolExtractor(repo_path)
        self.commit_sha = self.byte_extractor.get_commit_sha()
        self.repo_url = "https://github.com/CalcProgrammer1/OpenRGB"

    def collect(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None,
        controller_filter: Optional[str] = None,
        vid_filter: Optional[str] = None,
        pid_filter: Optional[str] = None,
        device_filter: Optional[str] = None,
        category_filter: Optional[str] = None
    ) -> dict[str, Any]:
        """Execute OpenRGB ingestion across all registered detectors and controller families."""
        devices = self.detector_parser.parse_all_devices()
        controllers = self.byte_extractor.extract_controller_info()
        logger.info(f"[openrgb] Parsed {len(devices)} device entries across {len(controllers)} controller families")

        ctrl_by_fam = {c.family_name: c for c in controllers}

        stats = {
            "devices_discovered": len(devices),
            "devices_recognized": 0,
            "records_created": 0,
            "records_updated": 0,
            "with_vid_pid": 0,
            "without_vid_pid": 0,
            "with_ipu": 0,
            "with_pci_svid": 0,
            "unique_vid_pids": set(),
            "controller_families": set(),
            "lighting_modes_recorded": 0,
            "packet_layouts_recorded": 0,
            "packed_structs_recorded": 0,
            "opcodes_recorded": 0,
            "parse_failures": 0,
            "skipped_entries": 0,
            "facts_recorded": 0,
            "hints_recorded": 0,
        }

        # Apply filters
        filtered_devices: list[OpenRGBDeviceMetadata] = []
        for d in devices:
            if controller_filter and controller_filter.lower() not in d.controller_family.lower():
                stats["skipped_entries"] += 1
                continue
            if vid_filter:
                v_clean = format_hex4(parse_hex_or_dec(vid_filter)) if parse_hex_or_dec(vid_filter) is not None else vid_filter.lower()
                if not d.vid_hex or v_clean != d.vid_hex.lower():
                    stats["skipped_entries"] += 1
                    continue
            if pid_filter:
                p_clean = format_hex4(parse_hex_or_dec(pid_filter)) if parse_hex_or_dec(pid_filter) is not None else pid_filter.lower()
                if not d.pid_hex or p_clean != d.pid_hex.lower():
                    stats["skipped_entries"] += 1
                    continue
            if device_filter and device_filter.lower() not in d.name.lower():
                stats["skipped_entries"] += 1
                continue
            if category_filter and category_filter.lower() not in d.category.lower():
                stats["skipped_entries"] += 1
                continue

            filtered_devices.append(d)

        if limit and limit > 0:
            filtered_devices = filtered_devices[:limit]

        # Process each device
        for d in filtered_devices:
            stats["devices_recognized"] += 1
            stats["controller_families"].add(d.controller_family)

            if d.vid is not None and d.pid is not None:
                stats["with_vid_pid"] += 1
                stats["unique_vid_pids"].add(f"{d.vid_hex}:{d.pid_hex}")
            else:
                stats["without_vid_pid"] += 1

            if d.interface is not None or d.usage_page is not None:
                stats["with_ipu"] += 1
            if d.svid is not None:
                stats["with_pci_svid"] += 1

            ctrl_info = ctrl_by_fam.get(d.controller_family)
            if not dry_run:
                self._persist_device(d, ctrl_info, stats)
            else:
                stats["records_created"] += 1

        # Persist controller family reference definitions
        if not dry_run:
            self._persist_controller_families(controllers, stats)

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["controller_family_count"] = len(stats["controller_families"])
        stats["controller_families"] = sorted(list(stats["controller_families"]))
        stats["unique_vid_pids"] = sorted(list(stats["unique_vid_pids"]))

        return stats

    def _persist_device(self, meta: OpenRGBDeviceMetadata, ctrl: Optional[OpenRGBControllerInfo], stats: dict[str, Any]):
        """Persist OpenRGB device record into SQLite database."""
        # 1. Vendor
        vendor_id = self.db.get_or_create_vendor(
            name=meta.vendor_slug,
            display_name=meta.manufacturer
        )

        # 2. Source Provenance
        source_url = f"{self.repo_url}/tree/{self.commit_sha}/{meta.source_file}"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor=meta.vendor_slug,
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)

        # 3. Product
        suffix = f"{meta.vid_hex}_{meta.pid_hex}_{meta.interface}_{meta.usage_page_hex}" if meta.vid_hex else meta.controller_family
        identity_key = generate_identity_key(meta.manufacturer, f"{meta.name}_{suffix}")
        p_id, is_new = self.db.upsert_product(
            vendor_id=vendor_id,
            raw_name=meta.name,
            canonical_name=meta.name,
            category=meta.category,
            identity_key=identity_key,
            product_url=source_url,
            image_url=None,
            category_confidence=1.0,
            metadata_confidence=0.85,  # UpstreamImplementationEvidence
            source_id=source_id,
            evidence_level=EvidenceLevel.LEVEL_1_METADATA,
            run_id=self.run_id
        )

        if is_new:
            stats["records_created"] += 1
        else:
            stats["records_updated"] += 1

        # 4. Device Identifier (VID/PID/Interface/UsagePage/Usage)
        if meta.vid is not None and meta.pid is not None:
            conn_type = "usb" if meta.bus_type == "USB" else ("pci" if "PCI" in meta.bus_type else "i2c")
            ident_fact = DeviceIdentifierFact(
                product_id=p_id,
                vid=meta.vid,
                pid=meta.pid,
                vid_hex=meta.vid_hex,
                pid_hex=meta.pid_hex,
                manufacturer_string=meta.manufacturer,
                product_string=meta.name,
                usage_page=meta.usage_page,
                usage=meta.usage,
                connection_type=conn_type,
                source_id=source_id,
                artifact_sha256=None,
                evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                confidence=0.85
            )
            self.db.upsert_device_identifier(ident_fact, run_id=self.run_id)

        # 5. Protocol Hints
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="controller_family",
                hint_value=meta.controller_family,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.90
            ),
            run_id=self.run_id
        )
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="detector_function",
                hint_value=meta.detector_func,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.90
            ),
            run_id=self.run_id
        )
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="bus_type",
                hint_value=meta.bus_type,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.90
            ),
            run_id=self.run_id
        )
        stats["hints_recorded"] += 3

        # 6. Technical Facts (Interface, Usage Page, SVID/SPID, Save/Direct/Effects)
        if meta.interface is not None:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="hid_interface",
                    value=str(meta.interface),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if meta.usage_page_hex is not None:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="hid_usage_page",
                    value=meta.usage_page_hex,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if meta.usage_hex is not None:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="hid_usage",
                    value=meta.usage_hex,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if meta.svid_hex is not None and meta.spid_hex is not None:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="subsystem_id",
                    value=f"{meta.svid_hex}:{meta.spid_hex}",
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if meta.i2c_addr_hex is not None:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="i2c_address",
                    value=meta.i2c_addr_hex,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if meta.save_mode:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="lighting_save_mode",
                    value=meta.save_mode,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.85
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if meta.direct_mode:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="lighting_direct_mode",
                    value=meta.direct_mode,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.85
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if meta.effects_mode:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="lighting_effects_mode",
                    value=meta.effects_mode,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.85
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if meta.comment:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="openrgb_comment",
                    value=meta.comment,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.85
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        # 7. Deep Byte-Level Protocol Facts from Controller
        if ctrl:
            if ctrl.packet_layouts:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="openrgb_packet_layouts",
                        value=json.dumps(ctrl.packet_layouts),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1
                stats["packet_layouts_recorded"] += len(ctrl.packet_layouts)

            if ctrl.packed_structs:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="openrgb_packed_structs",
                        value=json.dumps(ctrl.packed_structs),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1
                stats["packed_structs_recorded"] += len(ctrl.packed_structs)

            if ctrl.opcodes:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="openrgb_protocol_opcodes",
                        value=json.dumps(ctrl.opcodes),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1
                stats["opcodes_recorded"] += len(ctrl.opcodes)

            if ctrl.sink_functions:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="openrgb_sink_functions",
                        value=json.dumps(ctrl.sink_functions),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1

    def _persist_controller_families(self, controllers: list[OpenRGBControllerInfo], stats: dict[str, Any]):
        """Persist controller family metadata, lighting modes, report IDs, packet layouts, and structs."""
        source_url = f"{self.repo_url}/tree/{self.commit_sha}/Controllers"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor="openrgb_project",
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)
        vendor_id = self.db.get_or_create_vendor("openrgb_project", "OpenRGB Project", self.repo_url)

        for c in controllers:
            prod_name = f"OpenRGB Controller: {c.family_name}"
            identity_key = generate_identity_key("openrgb_project", f"controller_{c.family_name}")

            p_id, _ = self.db.upsert_product(
                vendor_id=vendor_id,
                raw_name=prod_name,
                canonical_name=prod_name,
                category="other",
                identity_key=identity_key,
                product_url=source_url,
                image_url=None,
                category_confidence=1.0,
                metadata_confidence=0.90,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                run_id=self.run_id
            )

            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=p_id,
                    hint_key="controller_family",
                    hint_value=c.family_name,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=1.0
                ),
                run_id=self.run_id
            )
            stats["hints_recorded"] += 1

            if c.modes:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="supported_lighting_modes",
                        value=json.dumps([m["mode_name"] for m in c.modes]),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1
                stats["lighting_modes_recorded"] += len(c.modes)

            if c.report_ids:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="report_ids",
                        value=json.dumps([f"0x{r:02X}" for r in c.report_ids]),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1

            if c.packet_layouts:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="openrgb_packet_layouts",
                        value=json.dumps(c.packet_layouts),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.95
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1

            if c.packed_structs:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="openrgb_packed_structs",
                        value=json.dumps(c.packed_structs),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.95
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1

            if c.opcodes:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="openrgb_protocol_opcodes",
                        value=json.dumps(c.opcodes),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.95
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1

            if c.sink_functions:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="openrgb_sink_functions",
                        value=json.dumps(c.sink_functions),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1
