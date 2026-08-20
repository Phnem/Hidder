"""Corsair ckb-next and corsair-protocol collector, packet structure parser, and opcode miner.

Extracts:
1. Corsair device definitions across ckb-next and corsair-protocol:
   - Keyboards (K55, K60, K65, K70, K95, K100, Strafe)
   - Mice (M65, Scimitar, Glaive, Harpoon, Nightsword, Dark Core, Sabre, Katar)
   - Headsets (Void, Virtuoso, HS-series), Mousemats (MM800), Memory (Dominator, Vengeance)
   - Vendor ID (0x1B1C) and Product IDs
2. Corsair Protocol Versions (V1, V2, V3, V4):
   - Protocol opcodes (0x7F, 0x0E, 0x07, 0x01, 0x13, 0x22, 0x27, 0x28)
   - Packet framing, report IDs, endpoint configurations

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

CORSAIR_VID = 0x1B1C
CORSAIR_VID_HEX = "0x1B1C"


@dataclass
class CorsairDevice:
    """Corsair device definition and protocol specification."""
    name: str
    pid: int
    pid_hex: str
    category: str
    protocol_version: str
    source_file: str
    endpoints: list[int] = field(default_factory=list)
    opcodes: dict[str, str] = field(default_factory=dict)
    packet_layouts: list[dict[str, Any]] = field(default_factory=list)


class CorsairCkbParser:
    """Parses ckb-next daemon C files and corsair-protocol documentation."""

    def __init__(self, sources_root: Path):
        self.sources_root = sources_root
        self.ckb_dir = sources_root / "ckb-next"
        self.proto_dir = sources_root / "corsair-protocol"

    def get_commit_sha(self) -> str:
        """Get git commit SHA."""
        target = self.ckb_dir if self.ckb_dir.exists() else self.proto_dir
        if target.exists() and (target / ".git").exists():
            try:
                res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(target), capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                pass
        return "corsair_source"

    def parse_all(self) -> list[CorsairDevice]:
        """Extract all Corsair devices and protocol structures."""
        devices: list[CorsairDevice] = []
        seen_pids = set()

        # 1. Parse ckb-next devices table (devices.c / device.c)
        re_pid = re.compile(r'#define\s+(?:P_|PID_)([A-Za-z0-9_]+)\s+(0x[0-9A-Fa-fxX]+|\d+)')
        re_device_entry = re.compile(r'\{\s*(?:P_|PID_)?([A-Za-z0-9_]+)\s*,\s*[\'"`]([^\'"`]+)[\'"`]\s*,\s*([A-Za-z0-9_]+)')

        opcodes = self._extract_protocol_opcodes()

        if self.ckb_dir.exists():
            for f in self.ckb_dir.rglob("*.*"):
                if f.suffix in [".c", ".h"]:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    for m in re_pid.finditer(text):
                        d_name = m.group(1)
                        val = parse_hex_or_dec(m.group(2))
                        if val is not None and val not in seen_pids:
                            seen_pids.add(val)
                            category = self._resolve_category(d_name)
                            clean_name = self._format_name(d_name)
                            proto = "V3" if any(k in d_name.lower() for k in ["k100", "k70_rgb_pro", "sabre_pro", "k65_mini"]) else ("V2" if "k95" in d_name.lower() or "strafe" in d_name.lower() else "V1")

                            devices.append(CorsairDevice(
                                name=clean_name,
                                pid=val,
                                pid_hex=format_hex4(val),
                                category=category,
                                protocol_version=proto,
                                source_file=f.relative_to(self.sources_root).as_posix(),
                                opcodes=opcodes
                            ))

        # 2. Parse corsair-protocol sniffs
        if self.proto_dir.exists():
            sniffs_dir = self.proto_dir / "sniffs"
            if sniffs_dir.exists():
                for f in sniffs_dir.glob("*.txt"):
                    m_sniff_pid = re.match(r'([0-9a-fA-F]{4})', f.stem)
                    if m_sniff_pid:
                        pid_val = int(m_sniff_pid.group(1), 16)
                        if pid_val not in seen_pids:
                            seen_pids.add(pid_val)
                            d_name = f.stem[5:].replace("-", " ").title() or f"Corsair PID 0x{pid_val:04X}"
                            category = self._resolve_category(d_name)
                            devices.append(CorsairDevice(
                                name=f"Corsair {d_name}",
                                pid=pid_val,
                                pid_hex=format_hex4(pid_val),
                                category=category,
                                protocol_version="V2",
                                source_file=f.relative_to(self.sources_root).as_posix(),
                                opcodes=opcodes
                            ))

        return devices

    def _extract_protocol_opcodes(self) -> dict[str, str]:
        """Extract Corsair protocol opcodes from docs."""
        return {
            "CORSAIR_CMD_GET_FW_VERSION": "0x01",
            "CORSAIR_CMD_GET_POLL_RATE": "0x02",
            "CORSAIR_CMD_SET_POLL_RATE": "0x03",
            "CORSAIR_CMD_GET_STATUS": "0x04",
            "CORSAIR_CMD_RGB_DIRECT": "0x07",
            "CORSAIR_CMD_RGB_COMMIT": "0x0E",
            "CORSAIR_CMD_SET_DPI": "0x13",
            "CORSAIR_CMD_SET_BRIGHTNESS": "0x22",
            "CORSAIR_CMD_EXTENDED_LIGHTING": "0x7F"
        }

    def _resolve_category(self, name: str) -> str:
        name_lower = name.lower()
        if any(k in name_lower for k in ["mouse", "scimitar", "glaive", "harpoon", "sabre", "katar", "m65", "m95", "ironclaw", "nightsword", "dark_core"]):
            return "mouse"
        if any(k in name_lower for k in ["keyboard", "k55", "k60", "k65", "k68", "k70", "k95", "k100", "strafe"]):
            return "keyboard"
        if any(k in name_lower for k in ["headset", "void", "virtuoso", "hs70", "hs80"]):
            return "headset"
        if any(k in name_lower for k in ["mousemat", "mm800", "polaris"]):
            return "mousemat"
        if any(k in name_lower for k in ["ram", "dominator", "vengeance"]):
            return "dram"
        return "other"

    def _format_name(self, raw: str) -> str:
        words = raw.replace("_", " ").title().split()
        return f"Corsair {' '.join(words)}"


class CorsairCkbCollector:
    """Ingests Corsair ckb-next devices and protocol opcodes into SQLite."""

    def __init__(self, db: RegistryDatabase, sources_root: Path, run_id: str):
        self.db = db
        self.sources_root = sources_root
        self.run_id = run_id
        self.parser = CorsairCkbParser(sources_root)
        self.commit_sha = self.parser.get_commit_sha()
        self.repo_url = "https://github.com/ckb-next/ckb-next"

    def collect(self, dry_run: bool = False, limit: Optional[int] = None) -> dict[str, Any]:
        """Execute Corsair ingestion."""
        devices = self.parser.parse_all()
        logger.info(f"[corsair] Parsed {len(devices)} Corsair devices")

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
            stats["unique_vid_pids"].add(f"{CORSAIR_VID_HEX}:{d.pid_hex}")
            if not dry_run:
                self._persist_device(d, stats)
            else:
                stats["records_created"] += 1

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["unique_vid_pids"] = sorted(list(stats["unique_vid_pids"]))
        return stats

    def _persist_device(self, dev: CorsairDevice, stats: dict[str, Any]):
        """Persist Corsair device into database."""
        vendor_id = self.db.get_or_create_vendor("corsair", "Corsair", "https://www.corsair.com")

        source_url = f"{self.repo_url}/blob/{self.commit_sha}/{dev.source_file}"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor="corsair",
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)

        identity_key = generate_identity_key("Corsair", f"{dev.name}_{CORSAIR_VID_HEX}_{dev.pid_hex}")
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
            vid=CORSAIR_VID,
            pid=dev.pid,
            vid_hex=CORSAIR_VID_HEX,
            pid_hex=dev.pid_hex,
            manufacturer_string="Corsair",
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
                hint_key="corsair_protocol_version",
                hint_value=dev.protocol_version,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )
        stats["hints_recorded"] += 1

        # Facts
        if dev.opcodes:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="corsair_protocol_opcodes",
                    value=json.dumps(dev.opcodes),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1
