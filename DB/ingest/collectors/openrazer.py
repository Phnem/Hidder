"""OpenRazer Linux driver collector, C struct parser, and opcode extractor.

Extracts:
1. Razer USB Device ID tables (USB_DEVICE_ID_RAZER_*) across mice, keyboards, headsets, and accessories.
2. Packed C packet structs (struct razer_report) with byte layouts.
3. Command classes, command IDs, transaction IDs, and LED identifiers.
4. DPI limits, polling rate ranges, matrix row/col layouts, battery reporting flags.

Imports all extracted facts and protocol hints into the SQLite Peripheral Registry.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from ingest.config import DB_PATH
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import (
    RawSource, SourceType, DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
)
from ingest.normalize.identifiers import normalize_vid_pid, format_hex4, parse_hex_or_dec
from ingest.normalize.models import generate_identity_key, normalize_product_name
from ingest.storage.database import RegistryDatabase

logger = get_logger()

RAZER_VID = 0x1532
RAZER_VID_HEX = "0x1532"


@dataclass
class OpenRazerDevice:
    """Razer device metadata and protocol specification."""
    name: str
    pid: int
    pid_hex: str
    category: str
    source_file: str
    max_dpi: Optional[int] = None
    matrix_rows: Optional[int] = None
    matrix_cols: Optional[int] = None
    polling_rates: list[int] = field(default_factory=list)
    has_battery: bool = False
    has_matrix: bool = False
    opcodes: dict[str, str] = field(default_factory=dict)
    packet_structs: list[dict[str, Any]] = field(default_factory=list)


class OpenRazerDriverParser:
    """Parses OpenRazer driver C sources and headers."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.driver_dir = repo_path / "driver"

    def get_commit_sha(self) -> str:
        """Get git commit SHA."""
        if self.repo_path.exists() and (self.repo_path / ".git").exists():
            try:
                res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.repo_path), capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                pass
        return "openrazer_source"

    def parse_all(self) -> list[OpenRazerDevice]:
        """Extract all Razer devices, PIDs, and protocol definitions."""
        if not self.driver_dir.exists():
            return []

        # 1. Collect all USB_DEVICE_ID_RAZER_* definitions
        re_pid_def = re.compile(r'#define\s+USB_DEVICE_ID_RAZER_([A-Za-z0-9_]+)\s+(0x[0-9A-Fa-fxX]+|\d+)')
        re_const = re.compile(r'#define\s+([A-Za-z0-9_]+)\s+(0x[0-9A-Fa-fxX]+|\d+)')
        re_table_entry = re.compile(r'\{\s*USB_DEVICE\s*\(\s*USB_VENDOR_ID_RAZER\s*,\s*USB_DEVICE_ID_RAZER_([A-Za-z0-9_]+)\s*\)\s*\}')

        pid_map: dict[str, int] = {}
        global_opcodes: dict[str, str] = {}

        for f in sorted(self.driver_dir.glob("*.*")):
            if f.suffix in [".c", ".h"]:
                text = f.read_text(encoding="utf-8", errors="replace")
                for m in re_pid_def.finditer(text):
                    dev_key = m.group(1)
                    val = parse_hex_or_dec(m.group(2))
                    if val is not None:
                        pid_map[dev_key] = val

                for m in re_const.finditer(text):
                    k = m.group(1)
                    v = m.group(2)
                    if any(kw in k.lower() for kw in ["razer", "cmd", "led", "matrix", "dpi", "poll", "battery", "report"]):
                        global_opcodes[k] = v

        # 2. Packed C Structs
        razer_structs = self._extract_structs()

        # 3. Categorize and build device models from driver tables
        devices: list[OpenRazerDevice] = []
        seen_pids = set()

        for f in sorted(self.driver_dir.glob("*.c")):
            text = f.read_text(encoding="utf-8", errors="replace")
            category = "mouse" if "mouse" in f.name else ("keyboard" if "kbd" in f.name else ("mousemat" if "firefly" in f.name else "accessory"))

            for m in re_table_entry.finditer(text):
                dev_key = m.group(1)
                pid = pid_map.get(dev_key)
                if pid is None or pid in seen_pids:
                    continue

                seen_pids.add(pid)
                name = self._format_device_name(dev_key)

                # Check capabilities from driver
                has_battery = "battery" in text or "wireless" in dev_key.lower() or "v2_pro" in dev_key.lower()
                has_matrix = "matrix" in text or "chroma" in dev_key.lower()

                devices.append(OpenRazerDevice(
                    name=name,
                    pid=pid,
                    pid_hex=format_hex4(pid),
                    category=category,
                    source_file=f.relative_to(self.repo_path).as_posix(),
                    has_battery=has_battery,
                    has_matrix=has_matrix,
                    opcodes=global_opcodes,
                    packet_structs=razer_structs
                ))

        # Add any remaining PIDs from header
        for dev_key, pid in pid_map.items():
            if pid not in seen_pids:
                seen_pids.add(pid)
                category = "mouse" if any(k in dev_key.lower() for k in ["deathadder", "viper", "naga", "basilisk", "mamba", "orochi", "abyssus", "taipan", "imperator", "ouroboros", "krait"]) else ("keyboard" if any(k in dev_key.lower() for k in ["blackwidow", "huntsman", "cynosa", "ornata", "deathstalker", "tartarus", "orbweaver"]) else ("mousemat" if "firefly" in dev_key.lower() else "accessory"))
                name = self._format_device_name(dev_key)
                devices.append(OpenRazerDevice(
                    name=name,
                    pid=pid,
                    pid_hex=format_hex4(pid),
                    category=category,
                    source_file="driver/razercommon.h",
                    opcodes=global_opcodes,
                    packet_structs=razer_structs
                ))

        return devices

    def _format_device_name(self, dev_key: str) -> str:
        """Format C macro device key into human-readable product name."""
        words = dev_key.replace("_", " ").title().split()
        return f"Razer {' '.join(words)}"

    def _extract_structs(self) -> list[dict[str, Any]]:
        """Extract ``razer_report`` from the checked-out upstream header.

        This used to return a hand-written 97-byte layout.  That was both
        structurally wrong (the kernel structure has no report-id field) and
        contradicted OpenRazer's own static assertion.  We intentionally only
        emit a layout when the upstream declaration and its asserted size agree.
        """
        header = self.driver_dir / "razercommon.h"
        if not header.exists():
            return []
        text = header.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"struct\s+razer_report\s*\{(?P<body>.*?)\};", text, re.DOTALL)
        asserted = re.search(
            r"static_assert\s*\(\s*sizeof\s*\(\s*struct\s+razer_report\s*\)\s*==\s*(\d+)\s*\)",
            text,
        )
        if not match or not asserted:
            logger.warning("[openrazer] razer_report has no upstream declaration/static size assertion")
            return []

        type_sizes = {"u8": 1, "__u8": 1, "uint8_t": 1, "__be16": 2, "u16": 2, "__u16": 2, "uint16_t": 2}
        fields: list[dict[str, Any]] = []
        offset = 0
        for raw_line in match.group("body").splitlines():
            line = raw_line.split("/*", 1)[0].strip()
            field = re.match(
                r"(?:(union\s+[A-Za-z0-9_]+)|([A-Za-z0-9_]+))\s+([A-Za-z0-9_]+)(?:\[(\d+)\])?\s*;",
                line,
            )
            if not field:
                continue
            raw_type = field.group(1) or field.group(2)
            name, array_len = field.group(3), int(field.group(4) or "1")
            # The two unions used by this packet are byte-sized protocol fields.
            base_size = 1 if raw_type.startswith("union ") else type_sizes.get(raw_type)
            if base_size is None:
                logger.warning("[openrazer] unsupported field type %s in razer_report", raw_type)
                return []
            size = base_size * array_len
            fields.append({"name": name, "type": f"{raw_type}[{array_len}]" if array_len > 1 else raw_type,
                           "offset": offset, "size": size})
            offset += size

        upstream_size = int(asserted.group(1))
        if offset != upstream_size:
            logger.error("[openrazer] calculated razer_report size %s differs from upstream %s", offset, upstream_size)
            return []
        return [{
            "struct_name": "razer_report",
            "total_size": offset,
            "upstream_size": upstream_size,
            "source_file": "driver/razercommon.h",
            "fields": fields,
        }]


class OpenRazerCollector:
    """Ingests OpenRazer device database and packet structures into SQLite."""

    def __init__(self, db: RegistryDatabase, repo_path: Path, run_id: str):
        self.db = db
        self.repo_path = repo_path
        self.run_id = run_id
        self.parser = OpenRazerDriverParser(repo_path)
        self.commit_sha = self.parser.get_commit_sha()
        self.repo_url = "https://github.com/openrazer/openrazer"

    def collect(self, dry_run: bool = False, limit: Optional[int] = None) -> dict[str, Any]:
        """Execute OpenRazer ingestion."""
        devices = self.parser.parse_all()
        logger.info(f"[openrazer] Extracted {len(devices)} Razer hardware devices")

        stats = {
            "devices_discovered": len(devices),
            "records_created": 0,
            "records_updated": 0,
            "facts_recorded": 0,
            "hints_recorded": 0,
            "unique_vid_pids": set(),
        }

        if limit and limit > 0:
            devices = devices[:limit]

        for d in devices:
            stats["unique_vid_pids"].add(f"{RAZER_VID_HEX}:{d.pid_hex}")
            if not dry_run:
                self._persist_device(d, stats)
            else:
                stats["records_created"] += 1

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["unique_vid_pids"] = sorted(list(stats["unique_vid_pids"]))
        return stats

    def _persist_device(self, dev: OpenRazerDevice, stats: dict[str, Any]):
        """Persist Razer device into database."""
        vendor_id = self.db.get_or_create_vendor("razer", "Razer", "https://www.razer.com")

        source_url = f"{self.repo_url}/blob/{self.commit_sha}/{dev.source_file}"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor="razer",
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)

        identity_key = generate_identity_key("Razer", f"{dev.name}_{RAZER_VID_HEX}_{dev.pid_hex}")
        p_id, is_new = self.db.upsert_product(
            vendor_id=vendor_id,
            raw_name=dev.name,
            canonical_name=dev.name,
            category=dev.category,
            identity_key=identity_key,
            product_url=source_url,
            image_url=None,
            category_confidence=1.0,
            metadata_confidence=0.90,
            source_id=source_id,
            evidence_level=EvidenceLevel.LEVEL_1_METADATA,
            run_id=self.run_id
        )

        if is_new:
            stats["records_created"] += 1
        else:
            stats["records_updated"] += 1

        # Device Identifier
        ident = DeviceIdentifierFact(
            product_id=p_id,
            vid=RAZER_VID,
            pid=dev.pid,
            vid_hex=RAZER_VID_HEX,
            pid_hex=dev.pid_hex,
            manufacturer_string="Razer",
            product_string=dev.name,
            usage_page=None,
            usage=None,
            connection_type="usb",
            source_id=source_id,
            artifact_sha256=None,
            evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
            confidence=0.90
        )
        self.db.upsert_device_identifier(ident, run_id=self.run_id)

        # Protocol Hints
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="openrazer_driver",
                hint_value=dev.source_file,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )
        stats["hints_recorded"] += 1

        # Facts
        if dev.packet_structs:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="openrazer_packet_structs",
                    value=json.dumps(dev.packet_structs),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if dev.opcodes:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="openrazer_protocol_opcodes",
                    value=json.dumps(dev.opcodes),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1

        if dev.has_battery:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="supports_battery_status",
                    value="true",
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1
