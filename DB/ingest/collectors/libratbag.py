"""Libratbag metadata collector and protocol extractor for Peripheral Registry and Protocol Miner.

Extracts device metadata (.device files) and byte-level protocol definitions
(C structs, enums, opcodes, report IDs, registers, semantic mappings) from the
official libratbag repository, and imports them into the SQLite database.
"""

from __future__ import annotations

import configparser
import copy
import json
import os
import re
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any, Generator

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

# Type byte sizes in standard C
C_TYPE_SIZES: dict[str, int] = {
    "uint8_t": 1, "int8_t": 1, "char": 1, "bool": 1, "unsigned char": 1, "signed char": 1,
    "uint16_t": 2, "int16_t": 2, "short": 2, "unsigned short": 2,
    "uint32_t": 4, "int32_t": 4, "int": 4, "unsigned int": 4, "unsigned": 4, "long": 4,
    "uint64_t": 8, "int64_t": 8, "long long": 8, "unsigned long long": 8,
}

SEMANTIC_PATTERNS = [
    (re.compile(r'\b(?:dpi|resolution|cpi|res)\b', re.I), "mouse.dpi"),
    (re.compile(r'\b(?:rate|poll|polling|refresh_rate|report_rate|hz)\b', re.I), "mouse.polling_rate"),
    (re.compile(r'\b(?:angle|snapping|angle_snap|anglesnapping)\b', re.I), "mouse.angle_snap"),
    (re.compile(r'\b(?:lod|lift|surface|calibration)\b', re.I), "mouse.lod"),
    (re.compile(r'\b(?:debounce|click_debounce)\b', re.I), "mouse.debounce"),
    (re.compile(r'\b(?:button|buttons|binding|bindings|action|key|keys|macro)\b', re.I), "mouse.buttons"),
    (re.compile(r'\b(?:led|rgb|light|lighting|color|brightness|breathing|spectrum)\b', re.I), "lighting.mode"),
    (re.compile(r'\b(?:battery|voltage|charge|charging|power|discharging)\b', re.I), "battery.status"),
    (re.compile(r'\b(?:profile|profiles|mode|modes|memory|onboard)\b', re.I), "profile.management"),
    (re.compile(r'\b(?:version|firmware|fw_version|build|revision|device_info|device_name)\b', re.I), "device.firmware"),
    (re.compile(r'\b(?:dfu|flash|bootloader|firmware_update|fw_update|upgrade)\b', re.I), "device.dfu"),
    (re.compile(r'\b(?:reset|reboot)\b', re.I), "device.reset"),
]

DFU_PATTERNS = re.compile(r'\b(?:dfu|flash|bootloader|firmware_update|fw_update|upgrade)\b', re.I)


@dataclass
class LibratbagDeviceMetadata:
    """Metadata extracted from a libratbag .device file."""
    file_name: str
    name: str
    bus: str
    vid: int
    pid: int
    vid_hex: str
    pid_hex: str
    driver: str
    device_type: str
    manufacturer: str
    vendor_slug: str
    display_name: str
    profiles: Optional[int] = None
    dpi_range: Optional[str] = None
    dpi_list: Optional[str] = None
    buttons: Optional[int] = None
    leds: Optional[int] = None
    quirks: list[str] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    sub_devices: list[dict[str, Any]] = field(default_factory=list)
    matches: list[tuple[str, int, int, str, str]] = field(default_factory=list)


@dataclass
class LibratbagPacketField:
    """Field within a C packet struct."""
    name: str
    type_name: str
    offset: int
    size: int
    array_len: Optional[int] = None
    endianness: str = "little"


@dataclass
class LibratbagPacketStruct:
    """Packet struct layout extracted from C header/source."""
    struct_name: str
    file_name: str
    total_size: int
    fields: list[LibratbagPacketField] = field(default_factory=list)


@dataclass
class LibratbagCommand:
    """Extracted command / opcode / feature definition."""
    protocol_family: str
    name: str
    opcode: int
    opcode_hex: str
    report_id: Optional[int] = None
    report_id_hex: Optional[str] = None
    semantic: str = "unknown"
    destructive_or_firmware_command: bool = False
    packet_length: Optional[int] = None
    direction: str = "bidirectional"
    source_file: str = ""
    line_number: Optional[int] = None


class LibratbagDeviceParser:
    """Parses .device INI-style files in libratbag/data/devices."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.devices_dir = repo_path / "data" / "devices"

        self._brand_lookup: dict[str, tuple[str, str]] = {}
        for b in ALL_CANONICAL_BRANDS:
            self._brand_lookup[b.slug.lower()] = (b.slug, b.canonical_name)
            self._brand_lookup[b.canonical_name.lower()] = (b.slug, b.canonical_name)
            for alias in b.aliases:
                self._brand_lookup[alias.lower()] = (b.slug, b.canonical_name)

    def list_device_files(self) -> list[Path]:
        """List all valid .device files (excluding examples and readmes)."""
        if not self.devices_dir.exists():
            return []
        files = []
        for p in self.devices_dir.glob("*.device"):
            if p.name == "device.example":
                continue
            files.append(p)
        return sorted(files)

    def parse_device_file(self, file_path: Path) -> Optional[LibratbagDeviceMetadata]:
        """Parse a single .device file into LibratbagDeviceMetadata."""
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(file_path, encoding="utf-8")
        except Exception as e:
            logger.error(f"[libratbag] Error reading device file {file_path.name}: {e}")
            return None

        if not cp.has_section("Device"):
            return None

        raw_name = cp.get("Device", "Name", fallback="").strip()
        match_str = cp.get("Device", "DeviceMatch", fallback="").strip()
        driver = cp.get("Device", "Driver", fallback="").strip()
        dev_type = cp.get("Device", "DeviceType", fallback="mouse").strip().lower()

        if not match_str:
            return None

        # Parse DeviceMatch (can be multiple semicolon-separated matches: bus:vid:pid;bus:vid:pid)
        all_matches: list[tuple[str, int, int, str, str]] = []
        for m_part in match_str.split(";"):
            m_part = m_part.strip()
            if not m_part:
                continue
            parts = m_part.split(":")
            if len(parts) < 3:
                continue
            bus_part = parts[0].strip().lower()
            raw_vid = parts[1].strip()
            raw_pid = parts[2].strip()

            vid_hex_str = raw_vid if raw_vid.lower().startswith("0x") else f"0x{raw_vid}"
            pid_hex_str = raw_pid if raw_pid.lower().startswith("0x") else f"0x{raw_pid}"

            norm = normalize_vid_pid(vid_hex_str, pid_hex_str)
            if norm:
                all_matches.append((bus_part, norm.vid, norm.pid, norm.vid_hex, norm.pid_hex))

        if not all_matches:
            return None

        primary_bus, primary_vid, primary_pid, primary_vid_hex, primary_pid_hex = all_matches[0]
        vendor_slug, display_name = self._resolve_vendor(raw_name, file_path.name, driver)

        # Parse driver section
        profiles: Optional[int] = None
        dpi_range: Optional[str] = None
        dpi_list: Optional[str] = None
        buttons: Optional[int] = None
        leds: Optional[int] = None
        quirks: list[str] = []
        capabilities: dict[str, Any] = {}
        sub_devices: list[dict[str, Any]] = []

        # Find driver section [Driver/<name>] or [Driver/<driver>/...]
        for sec in cp.sections():
            if sec.lower() == f"driver/{driver}".lower() or sec.lower() == "driver":
                if cp.has_option(sec, "Profiles"):
                    try:
                        profiles = int(cp.get(sec, "Profiles"))
                    except ValueError:
                        pass
                if cp.has_option(sec, "Buttons"):
                    try:
                        buttons = int(cp.get(sec, "Buttons"))
                    except ValueError:
                        pass
                if cp.has_option(sec, "Leds"):
                    try:
                        leds = int(cp.get(sec, "Leds"))
                    except ValueError:
                        pass
                if cp.has_option(sec, "DpiRange"):
                    dpi_range = cp.get(sec, "DpiRange").strip()
                if cp.has_option(sec, "DpiList"):
                    dpi_list = cp.get(sec, "DpiList").strip()
                if cp.has_option(sec, "Quirk") or cp.has_option(sec, "Quirks"):
                    opt_name = "Quirk" if cp.has_option(sec, "Quirk") else "Quirks"
                    for q in cp.get(sec, opt_name).split(";"):
                        if q.strip():
                            quirks.append(q.strip())
                if cp.has_option(sec, "Wireless"):
                    capabilities["wireless"] = cp.get(sec, "Wireless").strip() == "1"
                if cp.has_option(sec, "DeviceIndex"):
                    capabilities["device_index"] = cp.get(sec, "DeviceIndex").strip()
                if cp.has_option(sec, "Dpis"):
                    try:
                        capabilities["dpi_presets"] = int(cp.get(sec, "Dpis"))
                    except ValueError:
                        pass

            # Subdevices (e.g. [Driver/sinowealth/devices/<fw>])
            if "devices/" in sec.lower():
                fw_code = sec.split("/")[-1].strip()
                sub_meta: dict[str, Any] = {"fw_code": fw_code}
                if cp.has_option(sec, "DeviceName"):
                    sub_meta["name"] = cp.get(sec, "DeviceName").strip()
                if cp.has_option(sec, "Buttons"):
                    try:
                        sub_meta["buttons"] = int(cp.get(sec, "Buttons"))
                    except ValueError:
                        pass
                if cp.has_option(sec, "LedType"):
                    sub_meta["led_type"] = cp.get(sec, "LedType").strip()
                if cp.has_option(sec, "SensorType"):
                    sub_meta["sensor"] = cp.get(sec, "SensorType").strip()
                if cp.has_option(sec, "Profiles"):
                    try:
                        sub_meta["profiles"] = int(cp.get(sec, "Profiles"))
                    except ValueError:
                        pass
                sub_devices.append(sub_meta)

        return LibratbagDeviceMetadata(
            file_name=file_path.name,
            name=raw_name or file_path.stem.replace("-", " ").title(),
            bus=primary_bus,
            vid=primary_vid,
            pid=primary_pid,
            vid_hex=primary_vid_hex,
            pid_hex=primary_pid_hex,
            driver=driver,
            device_type=dev_type,
            manufacturer=display_name,
            vendor_slug=vendor_slug,
            display_name=display_name,
            profiles=profiles,
            dpi_range=dpi_range,
            dpi_list=dpi_list,
            buttons=buttons,
            leds=leds,
            quirks=sorted(set(quirks)),
            capabilities=capabilities,
            sub_devices=sub_devices,
            matches=all_matches
        )

    def _resolve_vendor(self, device_name: str, file_name: str, driver: str) -> tuple[str, str]:
        """Resolve vendor slug and canonical display name."""
        lower_name = device_name.lower()
        lower_file = file_name.lower()

        # Check canonical brands against device name or filename prefix
        for b_key, (slug, cname) in self._brand_lookup.items():
            if lower_name.startswith(b_key) or lower_file.startswith(b_key):
                return (slug, cname)

        if lower_file.startswith("logitech") or driver.startswith("hidpp") or driver.startswith("logitech"):
            return ("logitech", "Logitech")
        if lower_file.startswith("steelseries") or driver == "steelseries":
            return ("steelseries", "SteelSeries")
        if lower_file.startswith("asus") or driver == "asus":
            return ("asus", "ASUS")
        if lower_file.startswith("roccat") or driver.startswith("roccat"):
            return ("roccat", "Roccat")
        if lower_file.startswith("gskill") or driver == "gskill":
            return ("gskill", "G.Skill")
        if lower_file.startswith("glorious"):
            return ("glorious", "Glorious")
        if lower_file.startswith("etekcity") or driver == "etekcity":
            return ("etekcity", "Etekcity")
        if lower_file.startswith("marsgaming") or driver == "marsgaming":
            return ("marsgaming", "Mars Gaming")
        if lower_file.startswith("sinowealth") or driver.startswith("sinowealth"):
            return ("sinowealth", "SinoWealth")

        first_word = device_name.split()[0] if device_name else file_name.split("-")[0]
        slug = re.sub(r'[^a-z0-9]+', '_', first_word.lower()).strip('_')
        return (slug or "custom", first_word.title())


class LibratbagProtocolExtractor:
    """
    Extracts C structs, enums, #defines, and opcodes from libratbag/src.
    Computes byte-level offsets, lengths, and maps to semantic taxonomy.
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.src_dir = repo_path / "src"

    def get_commit_sha(self) -> str:
        """Get git commit SHA of libratbag repository."""
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

    def extract_all(self) -> tuple[list[LibratbagCommand], list[LibratbagPacketStruct]]:
        """Extract all protocol commands, opcodes, and packet layouts."""
        commands: list[LibratbagCommand] = []
        structs: list[LibratbagPacketStruct] = []

        c_files = list(self.src_dir.glob("*.h")) + list(self.src_dir.glob("*.c"))
        mars_dir = self.src_dir / "driver-marsgaming"
        if mars_dir.exists():
            c_files.extend(list(mars_dir.glob("*.*")))

        for cf in sorted(c_files):
            text = cf.read_text(encoding="utf-8", errors="replace")
            family = self._determine_family(cf.name)

            # 1. Extract Structs
            file_structs = self._extract_structs(text, cf.name)
            structs.extend(file_structs)

            # 2. Extract #defines
            file_commands_defs = self._extract_defines(text, cf.name, family)
            commands.extend(file_commands_defs)

            # 3. Extract enums
            file_commands_enums = self._extract_enums(text, cf.name, family)
            commands.extend(file_commands_enums)

        # Deduplicate commands by (protocol_family, report_id, opcode, name)
        deduped_commands: list[LibratbagCommand] = []
        seen = set()
        for cmd in commands:
            key = (cmd.protocol_family, cmd.report_id, cmd.opcode, cmd.name)
            if key not in seen:
                seen.add(key)
                deduped_commands.append(cmd)

        return deduped_commands, structs

    def _determine_family(self, filename: str) -> str:
        """Determine protocol family from source filename."""
        fn = filename.lower()
        if "hidpp10" in fn:
            return "hidpp10"
        if "hidpp20" in fn:
            return "hidpp20"
        if "hidpp" in fn:
            return "hidpp_generic"
        if "sinowealth" in fn:
            return "sinowealth"
        if "steelseries" in fn:
            return "steelseries"
        if "asus" in fn:
            return "asus"
        if "roccat" in fn:
            return "roccat"
        if "gskill" in fn:
            return "gskill"
        if "etekcity" in fn:
            return "etekcity"
        if "g300" in fn:
            return "logitech_g300"
        if "g600" in fn:
            return "logitech_g600"
        if "marsgaming" in fn:
            return "marsgaming"
        if "openinput" in fn:
            return "openinput"
        return "libratbag_core"

    def _extract_structs(self, text: str, filename: str) -> list[LibratbagPacketStruct]:
        """Extract packet struct definitions with field offsets and widths."""
        structs = []
        re_struct = re.compile(
            r'struct\s+([A-Za-z0-9_]+)?\s*\{([^}]+)\}(?:\s*__attribute__\s*\(\s*\(\s*packed\s*\)\s*\))?',
            re.DOTALL
        )

        for m in re_struct.finditer(text):
            sname = m.group(1) or "anonymous"
            body = m.group(2)
            fields: list[LibratbagPacketField] = []
            cur_offset = 0

            for line in body.split(";"):
                line = re.sub(r'/\*.*?\*/', '', line, flags=re.DOTALL).strip()
                line = re.sub(r'//.*', '', line).strip()
                if not line:
                    continue

                f_m = re.match(r'^(?:struct\s+)?([A-Za-z0-9_]+)\s+([A-Za-z0-9_]+)(?:\[([0-9A-Za-z_]+)\])?', line)
                if f_m:
                    ftype = f_m.group(1)
                    fname = f_m.group(2)
                    farr = f_m.group(3)
                    elem_size = C_TYPE_SIZES.get(ftype, 1)

                    arr_len: Optional[int] = None
                    if farr:
                        if farr.isdigit():
                            arr_len = int(farr)
                        elif farr.startswith("0x") or farr.startswith("0X"):
                            try:
                                arr_len = int(farr, 16)
                            except ValueError:
                                arr_len = 1
                        else:
                            arr_len = 4  # Default fallback for symbolic macro array lengths
                        size = elem_size * arr_len
                    else:
                        size = elem_size

                    fields.append(LibratbagPacketField(
                        name=fname,
                        type_name=ftype,
                        offset=cur_offset,
                        size=size,
                        array_len=arr_len,
                        endianness="little"
                    ))
                    cur_offset += size

            if fields and cur_offset > 0:
                structs.append(LibratbagPacketStruct(
                    struct_name=sname,
                    file_name=filename,
                    total_size=cur_offset,
                    fields=fields
                ))

        return structs

    def _extract_defines(self, text: str, filename: str, family: str) -> list[LibratbagCommand]:
        """Extract opcodes, report IDs, and registers from #define macros."""
        commands = []
        re_define = re.compile(
            r'^\s*#\s*define\s+([A-Za-z0-9_]+)\s+(0x[0-9A-Fa-f]+|\d+|\(1\s*<<\s*\d+\))\s*(?:/\*.*?\*/|//.*)?$',
            re.MULTILINE
        )

        for m in re_define.finditer(text):
            name = m.group(1).strip()
            val_str = m.group(2).strip()

            # Skip non-protocol defines
            if name.startswith("_") and not name.startswith("__ERROR"):
                continue

            opcode = self._parse_val(val_str)
            if opcode is None:
                continue

            semantic = self._map_semantic(name)
            is_dfu = bool(DFU_PATTERNS.search(re.sub(r'[^a-zA-Z0-9]+', ' ', name)))

            report_id: Optional[int] = None
            if "report_id" in name.lower() or name.startswith("REPORT_ID"):
                report_id = opcode

            commands.append(LibratbagCommand(
                protocol_family=family,
                name=name,
                opcode=opcode,
                opcode_hex=f"0x{opcode:02X}" if opcode < 256 else f"0x{opcode:04X}",
                report_id=report_id,
                report_id_hex=f"0x{report_id:02X}" if report_id is not None else None,
                semantic=semantic,
                destructive_or_firmware_command=is_dfu,
                source_file=filename
            ))

        return commands

    def _extract_enums(self, text: str, filename: str, family: str) -> list[LibratbagCommand]:
        """Extract enum constants (commands, features, error codes)."""
        commands = []
        re_enum = re.compile(r'enum\s+([A-Za-z0-9_]+)?\s*\{([^}]+)\}', re.MULTILINE | re.DOTALL)

        for m in re_enum.finditer(text):
            enum_name = m.group(1) or ""
            body = m.group(2)

            cur_val = 0
            for item in body.split(","):
                item = re.sub(r'/\*.*?\*/', '', item, flags=re.DOTALL).strip()
                item = re.sub(r'//.*', '', item).strip()
                if not item:
                    continue

                if "=" in item:
                    k, v = item.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    parsed = self._parse_val(v)
                    if parsed is not None:
                        cur_val = parsed
                else:
                    k = item.strip()

                if not k or not re.match(r'^[A-Za-z0-9_]+$', k):
                    continue

                semantic = self._map_semantic(f"{enum_name}_{k}")
                is_dfu = bool(DFU_PATTERNS.search(re.sub(r'[^a-zA-Z0-9]+', ' ', f"{enum_name}_{k}")))

                report_id: Optional[int] = None
                if "report_id" in k.lower() or "report_id" in enum_name.lower():
                    report_id = cur_val

                commands.append(LibratbagCommand(
                    protocol_family=family,
                    name=k,
                    opcode=cur_val,
                    opcode_hex=f"0x{cur_val:02X}" if cur_val < 256 else f"0x{cur_val:04X}",
                    report_id=report_id,
                    report_id_hex=f"0x{report_id:02X}" if report_id is not None else None,
                    semantic=semantic,
                    destructive_or_firmware_command=is_dfu,
                    source_file=filename
                ))
                cur_val += 1

        return commands

    def _parse_val(self, val_str: str) -> Optional[int]:
        """Parse hex, decimal, or bitshift value."""
        val_str = val_str.strip()
        if val_str.startswith("0x") or val_str.startswith("0X"):
            try:
                return int(val_str, 16)
            except ValueError:
                return None
        if val_str.isdigit():
            try:
                return int(val_str)
            except ValueError:
                return None
        # Bitshift: (1 << N) or 1 << N
        m = re.match(r'\(?1\s*<<\s*(\d+)\)?', val_str)
        if m:
            shift = int(m.group(1))
            return 1 << shift
        return None

    def _map_semantic(self, name: str) -> str:
        """Map technical name to semantic taxonomy."""
        clean = re.sub(r'[^a-zA-Z0-9]+', ' ', name)
        for pat, sem in SEMANTIC_PATTERNS:
            if pat.search(clean):
                return sem
        return "unknown"


class LibratbagCollector:
    """
    Ingests all libratbag devices and protocol definitions into SQLite Peripheral Registry.
    """

    def __init__(self, db: RegistryDatabase, repo_path: Path, run_id: str):
        self.db = db
        self.repo_path = repo_path
        self.run_id = run_id
        self.device_parser = LibratbagDeviceParser(repo_path)
        self.protocol_extractor = LibratbagProtocolExtractor(repo_path)
        self.commit_sha = self.protocol_extractor.get_commit_sha()
        self.repo_url = "https://github.com/libratbag/libratbag"

    def collect(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None,
        driver_filter: Optional[str] = None,
        vid_filter: Optional[str] = None,
        pid_filter: Optional[str] = None,
        device_filter: Optional[str] = None,
        family_filter: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Execute libratbag ingestion across .device files and driver protocol implementations.
        """
        # 1. Parse .device files
        device_files = self.device_parser.list_device_files()
        logger.info(f"[libratbag] Found {len(device_files)} .device files in {self.repo_path}")

        # 2. Extract Protocol Definitions
        commands, structs = self.protocol_extractor.extract_all()
        logger.info(f"[libratbag] Extracted {len(commands)} commands/opcodes and {len(structs)} packet structs")

        # Organize commands by family
        commands_by_family: dict[str, list[LibratbagCommand]] = {}
        for c in commands:
            commands_by_family.setdefault(c.protocol_family, []).append(c)

        stats = {
            "device_files_discovered": len(device_files),
            "devices_recognized": 0,
            "records_created": 0,
            "records_updated": 0,
            "with_vid_pid": 0,
            "without_vid_pid": 0,
            "unique_vid_pids": set(),
            "protocol_families": set(),
            "commands_extracted": len(commands),
            "report_ids_extracted": len([c for c in commands if c.report_id is not None]),
            "packet_layouts_extracted": len(structs),
            "capability_mappings": 0,
            "quirks_recorded": 0,
            "conflicts": 0,
            "parse_failures": 0,
            "skipped_entries": 0,
            "facts_recorded": 0,
            "hints_recorded": 0,
        }

        # Apply device filters
        devices_to_process: list[LibratbagDeviceMetadata] = []
        for df in device_files:
            meta = self.device_parser.parse_device_file(df)
            if not meta:
                stats["parse_failures"] += 1
                continue

            if driver_filter and driver_filter.lower() not in meta.driver.lower():
                stats["skipped_entries"] += 1
                continue
            if vid_filter:
                v_clean = format_hex4(parse_hex_or_dec(vid_filter)) if parse_hex_or_dec(vid_filter) is not None else vid_filter.lower()
                if v_clean != meta.vid_hex.lower():
                    stats["skipped_entries"] += 1
                    continue
            if pid_filter:
                p_clean = format_hex4(parse_hex_or_dec(pid_filter)) if parse_hex_or_dec(pid_filter) is not None else pid_filter.lower()
                if p_clean != meta.pid_hex.lower():
                    stats["skipped_entries"] += 1
                    continue
            if device_filter and device_filter.lower() not in meta.name.lower():
                stats["skipped_entries"] += 1
                continue
            if family_filter and family_filter.lower() not in meta.driver.lower():
                stats["skipped_entries"] += 1
                continue

            devices_to_process.append(meta)

        if limit and limit > 0:
            devices_to_process = devices_to_process[:limit]

        # Process each device
        for meta in devices_to_process:
            stats["devices_recognized"] += 1
            stats["protocol_families"].add(meta.driver)
            stats["with_vid_pid"] += 1
            stats["unique_vid_pids"].add(f"{meta.vid_hex}:{meta.pid_hex}")

            # Also handle sub-devices if defined
            for sub in meta.sub_devices:
                stats["devices_recognized"] += 1

            if not dry_run:
                self._persist_device(meta, commands_by_family.get(meta.driver, []), structs, stats)
            else:
                stats["records_created"] += 1 + len(meta.sub_devices)

        # Ingest family protocol definitions into protocol hints and facts
        if not dry_run:
            self._persist_protocol_families(commands, structs, stats)

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["protocol_family_count"] = len(stats["protocol_families"])
        stats["protocol_families"] = sorted(list(stats["protocol_families"]))
        stats["unique_vid_pids"] = sorted(list(stats["unique_vid_pids"]))

        return stats

    def _persist_device(
        self,
        meta: LibratbagDeviceMetadata,
        driver_commands: list[LibratbagCommand],
        structs: list[LibratbagPacketStruct],
        stats: dict[str, Any]
    ):
        """Persist normalized device record into SQLite database."""
        # 1. Vendor / Brand
        vendor_id = self.db.get_or_create_vendor(
            name=meta.vendor_slug,
            display_name=meta.display_name
        )

        # 2. Source Provenance
        source_url = f"{self.repo_url}/tree/{self.commit_sha}/data/devices/{meta.file_name}"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor=meta.vendor_slug,
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)

        # 3. Main Product
        identity_key = generate_identity_key(meta.display_name, f"{meta.name}_{meta.vid_hex}_{meta.pid_hex}")
        p_id, is_new = self.db.upsert_product(
            vendor_id=vendor_id,
            raw_name=meta.name,
            canonical_name=meta.name,
            category=meta.device_type,
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

        # 4. Device Identifiers (all matched VID/PIDs)
        for m_bus, m_vid, m_pid, m_vid_hex, m_pid_hex in (meta.matches or [(meta.bus, meta.vid, meta.pid, meta.vid_hex, meta.pid_hex)]):
            ident_fact = DeviceIdentifierFact(
                product_id=p_id,
                vid=m_vid,
                pid=m_pid,
                vid_hex=m_vid_hex,
                pid_hex=m_pid_hex,
                manufacturer_string=meta.manufacturer,
                product_string=meta.name,
                usage_page=None,
                usage=None,
                connection_type=m_bus,
                source_id=source_id,
                artifact_sha256=None,
                evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                confidence=0.85  # UpstreamImplementationEvidence
            )
            self.db.upsert_device_identifier(ident_fact, run_id=self.run_id)

        # 5. Protocol Hints
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="driver",
                hint_value=meta.driver,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.90
            ),
            run_id=self.run_id
        )
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="protocol_family",
                hint_value=meta.driver,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.90
            ),
            run_id=self.run_id
        )

        # 6. Technical Facts (Buttons, DPI, LEDs, Quirks)
        self.db.upsert_generic_fact(
            GenericFact(
                product_id=p_id,
                key="libratbag_device_file",
                value=meta.file_name,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                confidence=1.0
            ),
            run_id=self.run_id
        )

        if meta.buttons is not None:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="buttons_count",
                    value=str(meta.buttons),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            stats["capability_mappings"] += 1

        if meta.leds is not None:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="leds_count",
                    value=str(meta.leds),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            stats["capability_mappings"] += 1

        if meta.profiles is not None:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="profiles_count",
                    value=str(meta.profiles),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            stats["capability_mappings"] += 1

        if meta.dpi_range:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="dpi_range",
                    value=meta.dpi_range,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            stats["capability_mappings"] += 1

        if meta.dpi_list:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="dpi_list",
                    value=meta.dpi_list,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            stats["capability_mappings"] += 1

        for q in meta.quirks:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key=f"quirk:{q}",
                    value="true",
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["quirks_recorded"] += 1

        # 7. Sub-devices (e.g. SinoWealth firmware versions)
        for sub in meta.sub_devices:
            sub_name = sub.get("name", f"{meta.name} (FW {sub.get('fw_code')})")
            sub_key = generate_identity_key(meta.display_name, f"{sub_name}_{meta.vid_hex}_{meta.pid_hex}_{sub.get('fw_code')}")
            sub_p_id, sub_is_new = self.db.upsert_product(
                vendor_id=vendor_id,
                raw_name=sub_name,
                canonical_name=sub_name,
                category=meta.device_type,
                identity_key=sub_key,
                product_url=source_url,
                image_url=None,
                category_confidence=1.0,
                metadata_confidence=0.85,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                run_id=self.run_id
            )

            if sub_is_new:
                stats["records_created"] += 1
            else:
                stats["records_updated"] += 1

            # Sub-device identifier
            self.db.upsert_device_identifier(
                DeviceIdentifierFact(
                    product_id=sub_p_id,
                    vid=meta.vid,
                    pid=meta.pid,
                    vid_hex=meta.vid_hex,
                    pid_hex=meta.pid_hex,
                    manufacturer_string=meta.manufacturer,
                    product_string=sub_name,
                    usage_page=None,
                    usage=None,
                    connection_type=meta.bus,
                    source_id=source_id,
                    artifact_sha256=None,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.85
                ),
                run_id=self.run_id
            )

            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=sub_p_id,
                    hint_key="driver",
                    hint_value=meta.driver,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.90
                ),
                run_id=self.run_id
            )

            if "fw_code" in sub:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=sub_p_id,
                        key="firmware_code",
                        value=str(sub["fw_code"]),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                        confidence=0.95
                    ),
                    run_id=self.run_id
                )
            if "sensor" in sub:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=sub_p_id,
                        key="sensor_type",
                        value=str(sub["sensor"]),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
            if "buttons" in sub:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=sub_p_id,
                        key="buttons_count",
                        value=str(sub["buttons"]),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )

    def _persist_protocol_families(
        self,
        commands: list[LibratbagCommand],
        structs: list[LibratbagPacketStruct],
        stats: dict[str, Any]
    ):
        """Persist protocol family command and struct layout definitions."""
        # Create a synthetic source for libratbag driver source definitions
        source_url = f"{self.repo_url}/tree/{self.commit_sha}/src"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor="libratbag_project",
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)

        vendor_id = self.db.get_or_create_vendor("libratbag_project", "libratbag Project", self.repo_url)

        # For each protocol family, create or update a reference product representing the protocol architecture
        families = sorted(set(c.protocol_family for c in commands if c.protocol_family != "libratbag_core"))
        for fam in families:
            fam_commands = [c for c in commands if c.protocol_family == fam]
            fam_structs = [s for s in structs if fam in s.file_name.lower()]

            prod_name = f"Protocol Family: {fam}"
            identity_key = generate_identity_key("libratbag_project", f"family_{fam}")

            p_id, _ = self.db.upsert_product(
                vendor_id=vendor_id,
                raw_name=prod_name,
                canonical_name=prod_name,
                category="other",
                identity_key=identity_key,
                product_url=f"{self.repo_url}/tree/{self.commit_sha}/src",
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
                    hint_key="protocol_family",
                    hint_value=fam,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=1.0
                ),
                run_id=self.run_id
            )
            stats["hints_recorded"] += 1

            # Store commands as protocol hints and facts
            for cmd in fam_commands:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key=f"cmd:{cmd.name}",
                        value=json.dumps({
                            "opcode": cmd.opcode_hex,
                            "opcode_int": cmd.opcode,
                            "report_id": cmd.report_id_hex,
                            "semantic": cmd.semantic,
                            "destructive": cmd.destructive_or_firmware_command,
                            "source": cmd.source_file
                        }),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1

            # Store struct layouts
            for st in fam_structs:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key=f"struct:{st.struct_name}",
                        value=json.dumps({
                            "size": st.total_size,
                            "file": st.file_name,
                            "fields": [
                                {
                                    "name": f.name,
                                    "type": f.type_name,
                                    "offset": f.offset,
                                    "size": f.size,
                                    "array_len": f.array_len
                                }
                                for f in st.fields
                            ]
                        }),
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["facts_recorded"] += 1
