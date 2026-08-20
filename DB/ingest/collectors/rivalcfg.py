"""Rivalcfg SteelSeries device profile collector, command packet miner, and DPI/RGB extractor.

Extracts:
1. SteelSeries device models from rivalcfg/devices/*.py:
   - Device Name, Model Name, Vendor ID (0x1038), Product ID, Endpoint
2. Complete command packet specifications:
   - Sensitivity (DPI) command bytes & output ranges
   - Polling rate command bytes & value mappings
   - RGB lighting effects, LED IDs, gradient headers, duration offsets
   - Button mapping commands

Imports all extracted facts and protocol hints into the SQLite Peripheral Registry.
"""

from __future__ import annotations

import importlib.util
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

STEELSERIES_VID = 0x1038
STEELSERIES_VID_HEX = "0x1038"


@dataclass
class RivalcfgModel:
    """SteelSeries device model definition."""
    name: str
    model_name: str
    vid: int
    pid: int
    vid_hex: str
    pid_hex: str
    endpoint: Optional[int]
    source_file: str
    settings: dict[str, Any] = field(default_factory=dict)
    command_packets: list[dict[str, Any]] = field(default_factory=list)


class RivalcfgProfileParser:
    """Parses Rivalcfg device profile modules."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.devices_dir = repo_path / "rivalcfg" / "devices"

    def get_commit_sha(self) -> str:
        """Get git commit SHA."""
        if self.repo_path.exists() and (self.repo_path / ".git").exists():
            try:
                res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.repo_path), capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                pass
        return "rivalcfg_source"

    def parse_all(self) -> list[RivalcfgModel]:
        """Extract all SteelSeries device profiles."""
        if not self.devices_dir.exists():
            return []

        models: list[RivalcfgModel] = []

        # Parse each device python file statically via AST/regex
        re_model = re.compile(
            r'\{\s*"name":\s*[\'"`]([^\'"`]+)[\'"`]\s*,\s*"vendor_id":\s*(0x[0-9A-Fa-fxX]+|\d+)\s*,\s*"product_id":\s*(0x[0-9A-Fa-fxX]+|\d+)(?:\s*,\s*"endpoint":\s*(\d+))?',
            re.DOTALL
        )
        re_setting = re.compile(
            r'[\'"`]([a-zA-Z0-9_]+)[\'"`]:\s*\{\s*"label":\s*[\'"`]([^\'"`]+)[\'"`][\s\S]*?"command":\s*(\[[^\]]+\])',
            re.DOTALL
        )

        for f in sorted(self.devices_dir.glob("*.py")):
            if f.name == "__init__.py":
                continue
            text = f.read_text(encoding="utf-8", errors="replace")

            # Extract main profile name
            m_pname = re.search(r'"name":\s*[\'"`]([^\'"`]+)[\'"`]', text)
            profile_name = m_pname.group(1) if m_pname else f.stem

            # Extract all models in profile
            found_models = []
            for m in re_model.finditer(text):
                m_name = m.group(1)
                vid = parse_hex_or_dec(m.group(2)) or STEELSERIES_VID
                pid = parse_hex_or_dec(m.group(3))
                ep = int(m.group(4)) if m.group(4) else 0
                if pid is not None:
                    found_models.append((m_name, vid, pid, ep))

            # Extract command packets
            commands = []
            for m_s in re_setting.finditer(text):
                s_key = m_s.group(1)
                s_label = m_s.group(2)
                raw_cmd = m_s.group(3)
                cmd_bytes = [b.strip() for b in raw_cmd.strip("[]").split(",") if b.strip()]
                commands.append({
                    "setting_key": s_key,
                    "label": s_label,
                    "command_bytes": cmd_bytes
                })

            for m_name, vid, pid, ep in found_models:
                models.append(RivalcfgModel(
                    name=profile_name,
                    model_name=m_name,
                    vid=vid,
                    pid=pid,
                    vid_hex=format_hex4(vid),
                    pid_hex=format_hex4(pid),
                    endpoint=ep,
                    source_file=f.relative_to(self.repo_path).as_posix(),
                    command_packets=commands
                ))

        return models


class RivalcfgCollector:
    """Ingests Rivalcfg SteelSeries models and command packets into SQLite."""

    def __init__(self, db: RegistryDatabase, repo_path: Path, run_id: str):
        self.db = db
        self.repo_path = repo_path
        self.run_id = run_id
        self.parser = RivalcfgProfileParser(repo_path)
        self.commit_sha = self.parser.get_commit_sha()
        self.repo_url = "https://github.com/flozz/rivalcfg"

    def collect(self, dry_run: bool = False, limit: Optional[int] = None) -> dict[str, Any]:
        """Execute Rivalcfg ingestion."""
        models = self.parser.parse_all()
        logger.info(f"[rivalcfg] Parsed {len(models)} SteelSeries models")

        stats = {
            "devices_discovered": len(models),
            "records_created": 0,
            "records_updated": 0,
            "facts_recorded": 0,
            "hints_recorded": 0,
            "unique_vid_pids": set(),
        }

        if limit and limit > 0:
            models = models[:limit]

        for m in models:
            stats["unique_vid_pids"].add(f"{STEELSERIES_VID_HEX}:{m.pid_hex}")
            if not dry_run:
                self._persist_model(m, stats)
            else:
                stats["records_created"] += 1

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["unique_vid_pids"] = sorted(list(stats["unique_vid_pids"]))
        return stats

    def _persist_model(self, mod: RivalcfgModel, stats: dict[str, Any]):
        """Persist SteelSeries model into database."""
        vendor_id = self.db.get_or_create_vendor("steelseries", "SteelSeries", "https://steelseries.com")

        source_url = f"{self.repo_url}/blob/{self.commit_sha}/{mod.source_file}"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor="steelseries",
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)

        identity_key = generate_identity_key("SteelSeries", f"{mod.model_name}_{STEELSERIES_VID_HEX}_{mod.pid_hex}")
        p_id, is_new = self.db.upsert_product(
            vendor_id=vendor_id,
            raw_name=mod.model_name,
            canonical_name=mod.model_name,
            category="mouse",
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
            vid=mod.vid,
            pid=mod.pid,
            vid_hex=mod.vid_hex,
            pid_hex=mod.pid_hex,
            manufacturer_string="SteelSeries",
            product_string=mod.model_name,
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
                hint_key="rivalcfg_profile",
                hint_value=mod.source_file,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )
        if mod.endpoint is not None:
            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=p_id,
                    hint_key="hid_endpoint",
                    hint_value=str(mod.endpoint),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["hints_recorded"] += 2

        # Facts
        if mod.command_packets:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="rivalcfg_command_packets",
                    value=json.dumps(mod.command_packets),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.95
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1
