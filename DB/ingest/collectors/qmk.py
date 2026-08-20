"""QMK firmware collector and metadata inheritance resolver for Peripheral Registry.

Extracts factual technical metadata from official qmk/qmk_firmware repository,
resolves directory-tree inheritance (info.json, keyboard.json, rules.mk, config.h),
normalizes hardware facts (VID/PID, MCU, bootloader, matrix, features, layouts),
and imports into the existing Peripheral Registry database.
"""

import copy
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any, Generator

from ingest.brands.canonical import ALL_CANONICAL_BRANDS, get_brand_by_slug
from ingest.config import DB_PATH
from ingest.logging_setup import get_logger, log_discovery, log_fact, log_hint
from ingest.normalize.evidence import (
    RawSource, SourceType, DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
)
from ingest.normalize.identifiers import normalize_vid_pid, format_hex4, parse_hex_or_dec
from ingest.normalize.models import generate_identity_key, normalize_product_name
from ingest.storage.database import RegistryDatabase

logger = get_logger()

# Regex patterns for rules.mk parsing
RE_RULES_VAR = re.compile(r'^\s*([A-Za-z0-9_]+)\s*[:?]?=\s*(.*)$', re.MULTILINE)
RE_CONFIG_DEFINE = re.compile(r'^\s*#\s*define\s+([A-Za-z0-9_]+)(?:\s+(.*))?$', re.MULTILINE)

RULE_FEATURE_MAP = {
    "BOOTMAGIC_ENABLE": "bootmagic",
    "MOUSEKEY_ENABLE": "mousekey",
    "EXTRAKEY_ENABLE": "extrakey",
    "CONSOLE_ENABLE": "console",
    "COMMAND_ENABLE": "command",
    "BACKLIGHT_ENABLE": "backlight",
    "RGBLIGHT_ENABLE": "rgblight",
    "RGB_MATRIX_ENABLE": "rgb_matrix",
    "NKRO_ENABLE": "nkro",
    "AUDIO_ENABLE": "audio",
    "OLED_ENABLE": "oled",
    "ENCODER_ENABLE": "encoder",
    "DYNAMIC_KEYMAP_ENABLE": "dynamic_keymap",
    "VIA_ENABLE": "via",
    "VIAL_ENABLE": "vial",
    "RAW_ENABLE": "raw_hid",
    "DIP_SWITCH_ENABLE": "dip_switch",
    "HAPTIC_ENABLE": "haptic",
    "JOYSTICK_ENABLE": "joystick",
    "SECURE_ENABLE": "secure",
    "STENO_ENABLE": "stenography",
    "COMBO_ENABLE": "combo",
    "LEADER_ENABLE": "leader_key",
    "CAPS_WORD_ENABLE": "caps_word",
    "TAP_DANCE_ENABLE": "tap_dance",
}


@dataclass
class QmkTargetMetadata:
    """Normalized technical facts extracted for a specific QMK keyboard target."""
    target_path: str
    keyboard_name: str
    manufacturer: str
    vendor_slug: str
    display_name: str
    maintainer: Optional[str] = None
    url: Optional[str] = None
    revision: Optional[str] = None
    vid: Optional[int] = None
    pid: Optional[int] = None
    vid_hex: Optional[str] = None
    pid_hex: Optional[str] = None
    device_version: Optional[str] = None
    processor: Optional[str] = None
    bootloader: Optional[str] = None
    matrix_rows: Optional[int] = None
    matrix_cols: Optional[int] = None
    matrix_pins: Optional[dict[str, Any]] = None
    connectivity: list[str] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=dict)
    layouts: list[str] = field(default_factory=list)
    hardware_facts: dict[str, Any] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)


def deep_update_dict(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    """Recursively update dst with a deep copy of src, never mutating src."""
    for k, v in src.items():
        if isinstance(v, dict):
            if isinstance(dst.get(k), dict):
                deep_update_dict(dst[k], v)
            else:
                dst[k] = copy.deepcopy(v)
        elif isinstance(v, list):
            dst[k] = copy.deepcopy(v)
        else:
            dst[k] = v
    return dst


class QmkMetadataResolver:
    """
    Resolves effective metadata for QMK keyboard targets by traversing
    the directory hierarchy from root down to the leaf target folder.
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.keyboards_dir = repo_path / "keyboards"
        self._json_cache: dict[Path, Optional[dict[str, Any]]] = {}
        self._rules_cache: dict[Path, dict[str, str]] = {}
        self._config_cache: dict[Path, dict[str, str]] = {}

        # Pre-build lookup map for canonical brands
        self._brand_lookup: dict[str, tuple[str, str]] = {}
        for b in ALL_CANONICAL_BRANDS:
            self._brand_lookup[b.slug.lower()] = (b.slug, b.canonical_name)
            self._brand_lookup[b.canonical_name.lower()] = (b.slug, b.canonical_name)
            for alias in b.aliases:
                self._brand_lookup[alias.lower()] = (b.slug, b.canonical_name)

    def get_commit_sha(self) -> str:
        """Get git commit SHA of QMK repository."""
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

    def list_targets(self) -> list[str]:
        """
        Discover all valid QMK keyboard targets by finding all keyboard.json files
        outside of keymaps directories.
        """
        targets = []
        for p in self.keyboards_dir.rglob("keyboard.json"):
            if "keymaps" in p.parts:
                continue
            rel_path = p.parent.relative_to(self.keyboards_dir).as_posix()
            targets.append(rel_path)
        return sorted(targets)

    def _read_json(self, file_path: Path) -> Optional[dict[str, Any]]:
        if file_path in self._json_cache:
            return self._json_cache[file_path]
        if not file_path.is_file():
            self._json_cache[file_path] = None
            return None
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            # 1. Fast direct parse
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    self._json_cache[file_path] = data
                    return data
            except Exception:
                pass

            # 2. Resilient parse for non-standard JSONs (comments, trailing commas, missing commas)
            cleaned = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
            cleaned = re.sub(r'//.*', '', cleaned)
            cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
            cleaned = re.sub(r'([}\]"\d])\s*(\n\s*")', r'\1,\2', cleaned)
            cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
            data = json.loads(cleaned)
            if isinstance(data, dict):
                self._json_cache[file_path] = data
                return data
        except Exception as e:
            logger.debug(f"Error reading JSON {file_path}: {e}")
        self._json_cache[file_path] = None
        return None

    def _read_rules_mk(self, file_path: Path) -> dict[str, str]:
        if file_path in self._rules_cache:
            return self._rules_cache[file_path]
        rules: dict[str, str] = {}
        if not file_path.is_file():
            self._rules_cache[file_path] = rules
            return rules
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                for m in RE_RULES_VAR.finditer(content):
                    k = m.group(1).strip()
                    v = m.group(2).strip()
                    rules[k] = v
        except Exception as e:
            logger.debug(f"Error reading rules.mk {file_path}: {e}")
        self._rules_cache[file_path] = rules
        return rules

    def _read_config_h(self, file_path: Path) -> dict[str, str]:
        if file_path in self._config_cache:
            return self._config_cache[file_path]
        defines: dict[str, str] = {}
        if not file_path.is_file():
            self._config_cache[file_path] = defines
            return defines
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                for m in RE_CONFIG_DEFINE.finditer(content):
                    k = m.group(1).strip()
                    v = (m.group(2) or "").strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    defines[k] = v
        except Exception as e:
            logger.debug(f"Error reading config.h {file_path}: {e}")
        self._config_cache[file_path] = defines
        return defines

    def resolve_target_raw(self, target_path_str: str) -> tuple[dict[str, Any], list[str]]:
        """
        Resolve effective raw JSON metadata and rules/defines by cascading from root
        down to the leaf target path level by level.
        """
        parts = Path(target_path_str).parts
        levels: list[Path] = [self.keyboards_dir]
        cur = self.keyboards_dir
        for part in parts:
            cur = cur / part
            levels.append(cur)

        merged: dict[str, Any] = {}
        used_files: list[str] = []

        # Traverse directory hierarchy from root level down to leaf level
        for lvl in levels:
            # 1. Level JSONs (info.json first, then keyboard.json if present)
            for fname in ["info.json", "keyboard.json"]:
                f = lvl / fname
                data = self._read_json(f)
                if data:
                    deep_update_dict(merged, data)
                    used_files.append(str(f.relative_to(self.keyboards_dir).as_posix()))

            # 2. Level rules.mk
            r_f = lvl / "rules.mk"
            rules = self._read_rules_mk(r_f)
            if rules:
                used_files.append(str(r_f.relative_to(self.keyboards_dir).as_posix()))
                self._apply_level_rules(merged, rules)

            # 3. Level config.h
            c_f = lvl / "config.h"
            config = self._read_config_h(c_f)
            if config:
                used_files.append(str(c_f.relative_to(self.keyboards_dir).as_posix()))
                self._apply_level_config(merged, config)

        return merged, sorted(set(used_files))

    def _apply_level_rules(self, data: dict[str, Any], rules: dict[str, str]):
        """Apply rules.mk variables at current directory level."""
        if "MCU" in rules and rules["MCU"].strip():
            data["processor"] = rules["MCU"].strip()
        if "BOOTLOADER" in rules and rules["BOOTLOADER"].strip():
            data["bootloader"] = rules["BOOTLOADER"].strip()

        features = data.setdefault("features", {})
        for r_key, f_key in RULE_FEATURE_MAP.items():
            if r_key in rules:
                val_str = rules[r_key].lower().strip()
                features[f_key] = val_str in ["yes", "true", "1", "on"]

    def _apply_level_config(self, data: dict[str, Any], config: dict[str, str]):
        """Apply config.h #defines at current directory level."""
        usb = data.setdefault("usb", {})
        if "VENDOR_ID" in config and config["VENDOR_ID"].strip():
            usb["vid"] = config["VENDOR_ID"].strip()
        if "PRODUCT_ID" in config and config["PRODUCT_ID"].strip():
            usb["pid"] = config["PRODUCT_ID"].strip()
        if "DEVICE_VER" in config and config["DEVICE_VER"].strip():
            usb["device_version"] = config["DEVICE_VER"].strip()

        if "MANUFACTURER" in config and config["MANUFACTURER"].strip():
            data["manufacturer"] = config["MANUFACTURER"].strip()
        if "PRODUCT" in config and config["PRODUCT"].strip():
            data["keyboard_name"] = config["PRODUCT"].strip()

        matrix_size = data.setdefault("matrix_size", {})
        if "MATRIX_ROWS" in config:
            try:
                matrix_size["rows"] = int(config["MATRIX_ROWS"])
            except ValueError:
                pass
        if "MATRIX_COLS" in config:
            try:
                matrix_size["cols"] = int(config["MATRIX_COLS"])
            except ValueError:
                pass

    def resolve_target(self, target_path: str) -> QmkTargetMetadata:
        """Resolve, normalize, and validate full technical facts for a QMK target."""
        raw_data, source_files = self.resolve_target_raw(target_path)
        parts = Path(target_path).parts

        # 1. Manufacturer & Vendor Normalization
        raw_mfg = (raw_data.get("manufacturer") or "").strip()
        vendor_slug, display_name = self._resolve_vendor(raw_mfg, parts)

        # 2. Keyboard Name & Revision Normalization
        raw_name = (raw_data.get("keyboard_name") or "").strip()
        if not raw_name:
            raw_name = parts[-1].replace("_", " ").replace("-", " ").title()

        revision = self._extract_revision(target_path, parts)

        # 3. VID / PID Normalization
        usb_data = raw_data.get("usb", {}) if isinstance(raw_data.get("usb"), dict) else {}
        raw_vid = usb_data.get("vid")
        raw_pid = usb_data.get("pid")
        normalized_vid_pid = normalize_vid_pid(raw_vid, raw_pid) if (raw_vid is not None and raw_pid is not None) else None

        vid_int = normalized_vid_pid.vid if normalized_vid_pid else None
        pid_int = normalized_vid_pid.pid if normalized_vid_pid else None
        vid_hex = normalized_vid_pid.vid_hex if normalized_vid_pid else None
        pid_hex = normalized_vid_pid.pid_hex if normalized_vid_pid else None

        # 4. Device Version
        device_version = usb_data.get("device_version") or usb_data.get("device_ver")
        if device_version is not None:
            device_version = str(device_version).strip()

        # 5. Processor / MCU & Bootloader
        processor = raw_data.get("processor")
        if processor:
            processor = str(processor).strip()
            if processor.lower() == "unknown":
                processor = None

        bootloader = raw_data.get("bootloader")
        if bootloader:
            bootloader = str(bootloader).strip()
            if bootloader.lower() == "unknown":
                bootloader = None

        # 6. Matrix Dimensions
        matrix_rows, matrix_cols = self._extract_matrix_dimensions(raw_data)

        # 7. Connectivity
        connectivity = self._extract_connectivity(raw_data, normalized_vid_pid is not None)

        # 8. Feature Flags
        features = self._extract_features(raw_data)

        # 9. Layout Names
        layouts = self._extract_layout_names(raw_data)

        # 10. Hardware Facts & Capabilities
        hw_facts = self._extract_hardware_facts(raw_data)

        maintainer = raw_data.get("maintainer")
        if maintainer:
            maintainer = str(maintainer).strip()

        url = raw_data.get("url")
        if url:
            url = str(url).strip()

        return QmkTargetMetadata(
            target_path=target_path,
            keyboard_name=raw_name,
            manufacturer=raw_mfg or display_name,
            vendor_slug=vendor_slug,
            display_name=display_name,
            maintainer=maintainer,
            url=url,
            revision=revision,
            vid=vid_int,
            pid=pid_int,
            vid_hex=vid_hex,
            pid_hex=pid_hex,
            device_version=device_version,
            processor=processor,
            bootloader=bootloader,
            matrix_rows=matrix_rows,
            matrix_cols=matrix_cols,
            matrix_pins=raw_data.get("matrix_pins"),
            connectivity=connectivity,
            features=features,
            layouts=layouts,
            hardware_facts=hw_facts,
            source_files=source_files,
        )

    def _resolve_vendor(self, raw_mfg: str, parts: tuple[str, ...]) -> tuple[str, str]:
        """Resolve vendor slug and canonical display name."""
        # 1. Try matching raw_mfg against canonical brands
        if raw_mfg:
            mfg_clean = raw_mfg.strip().lower()
            if mfg_clean in self._brand_lookup:
                return self._brand_lookup[mfg_clean]

        # 2. Try matching top-level path segment against canonical brands
        if parts:
            top_clean = parts[0].strip().lower()
            if top_clean in self._brand_lookup:
                return self._brand_lookup[top_clean]
            # Special known folder aliases
            if top_clean == "gmmk":
                return ("glorious", "Glorious")
            if top_clean == "cannonkeys":
                return ("cannonkeys", "CannonKeys")
            if top_clean == "clueboard":
                return ("clueboard", "Clueboard")
            if top_clean == "dz60" or top_clean.startswith("kbd"):
                return ("kbdfans", "KBDfans")
            if top_clean == "ergodox_ez":
                return ("ergodox", "Ergodox EZ")
            if top_clean == "planck":
                return ("olkb", "OLKB")

        # 3. Fallback: generate clean slug and display name
        if raw_mfg:
            slug = re.sub(r'[^a-z0-9]+', '_', raw_mfg.lower()).strip('_')
            return (slug or "custom", raw_mfg)

        if parts:
            slug = re.sub(r'[^a-z0-9]+', '_', parts[0].lower()).strip('_')
            name = parts[0].replace("_", " ").title()
            return (slug or "custom", name)

        return ("custom", "Custom QMK Keyboard")

    def _extract_revision(self, target_path: str, parts: tuple[str, ...]) -> Optional[str]:
        """Extract revision / variant string from target path if present."""
        rev_patterns = [
            r'\b(rev\d+|rev_\w+|v\d+(?:_\d+)?|r\d+|ansi|iso|hotswap|soldered|solder|rgb|ble|bluetooth|pro|plus|max|lite|pcb|ortho|ergo)\b'
        ]
        for part in reversed(parts):
            for pat in rev_patterns:
                m = re.search(pat, part, re.IGNORECASE)
                if m:
                    return m.group(1).lower()
        return None

    def _extract_matrix_dimensions(self, data: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
        """Extract matrix rows and cols count from matrix_size or matrix_pins."""
        rows = None
        cols = None

        # Check matrix_size
        ms = data.get("matrix_size")
        if isinstance(ms, dict):
            r = ms.get("rows")
            c = ms.get("cols")
            if isinstance(r, int) and r > 0:
                rows = r
            if isinstance(c, int) and c > 0:
                cols = c

        # Fallback to matrix_pins
        mp = data.get("matrix_pins")
        if isinstance(mp, dict):
            if rows is None and isinstance(mp.get("rows"), list):
                rows = len(mp["rows"])
            if cols is None and isinstance(mp.get("cols"), list):
                cols = len(mp["cols"])
            if rows is None and cols is None and isinstance(mp.get("direct"), list):
                rows = len(mp["direct"])
                if rows > 0 and isinstance(mp["direct"][0], list):
                    cols = len(mp["direct"][0])

        return rows, cols

    def _extract_connectivity(self, data: dict[str, Any], has_usb_ids: bool) -> list[str]:
        """Extract connection types supported by this keyboard target."""
        conns = []
        if has_usb_ids or "usb" in data:
            conns.append("usb")

        if data.get("bluetooth") or (isinstance(data.get("features"), dict) and data["features"].get("bluetooth")):
            conns.append("bluetooth")

        if data.get("split") or (isinstance(data.get("features"), dict) and data["features"].get("split")):
            conns.append("split")

        if data.get("ps2") or (isinstance(data.get("features"), dict) and data["features"].get("ps2")):
            conns.append("ps2")

        if not conns:
            conns.append("usb")
        return sorted(set(conns))

    def _extract_features(self, data: dict[str, Any]) -> dict[str, bool]:
        """Extract boolean capability feature flags."""
        features: dict[str, bool] = {}

        # Raw features dictionary
        raw_feats = data.get("features")
        if isinstance(raw_feats, dict):
            for k, v in raw_feats.items():
                if isinstance(v, bool):
                    features[k] = v
                elif isinstance(v, (int, str)):
                    features[k] = str(v).lower() in ["true", "1", "yes", "on"]

        # Explicit sections implying capability
        if "encoder" in data and isinstance(data["encoder"], dict):
            if data["encoder"].get("rotary") or data["encoder"].get("enabled", True):
                features["encoder"] = True

        if "rgb_matrix" in data and isinstance(data["rgb_matrix"], dict):
            features["rgb_matrix"] = True

        if "rgblight" in data and isinstance(data["rgblight"], dict):
            features["rgblight"] = True

        if "backlight" in data and isinstance(data["backlight"], dict):
            features["backlight"] = True

        if "audio" in data and isinstance(data["audio"], dict):
            features["audio"] = True

        if "oled" in data and isinstance(data["oled"], dict):
            features["oled"] = True

        if "dynamic_keymap" in data and isinstance(data["dynamic_keymap"], dict):
            features["dynamic_keymap"] = True

        if "dip_switch" in data and isinstance(data["dip_switch"], dict):
            features["dip_switch"] = True

        if "haptic" in data and isinstance(data["haptic"], dict):
            features["haptic"] = True

        if "joystick" in data and isinstance(data["joystick"], dict):
            features["joystick"] = True

        if "secure" in data and isinstance(data["secure"], dict):
            features["secure"] = True

        if "stenography" in data and isinstance(data["stenography"], dict):
            features["stenography"] = True

        # Host default NKRO
        host = data.get("host")
        if isinstance(host, dict) and isinstance(host.get("default"), dict):
            if host["default"].get("nkro"):
                features["nkro"] = True

        return features

    def _extract_layout_names(self, data: dict[str, Any]) -> list[str]:
        """Extract layout macro names defined for this keyboard."""
        layouts = data.get("layouts")
        if isinstance(layouts, dict):
            return sorted([k for k in layouts.keys() if isinstance(k, str)])
        return []

    def _extract_hardware_facts(self, data: dict[str, Any]) -> dict[str, Any]:
        """Extract structured technical hardware facts (driver names, counts, etc.)."""
        facts: dict[str, Any] = {}

        # Encoders
        enc = data.get("encoder")
        if isinstance(enc, dict):
            rotary = enc.get("rotary")
            if isinstance(rotary, list):
                facts["encoder_count"] = len(rotary)
                resolutions = [r.get("resolution") for r in rotary if isinstance(r, dict) and "resolution" in r]
                if resolutions:
                    facts["encoder_resolution"] = resolutions[0]

        # RGB Matrix
        rgb_m = data.get("rgb_matrix")
        if isinstance(rgb_m, dict):
            if rgb_m.get("driver"):
                facts["rgb_matrix_driver"] = rgb_m["driver"]

        # RGBLight
        rgbl = data.get("rgblight")
        if isinstance(rgbl, dict):
            if rgbl.get("driver"):
                facts["rgblight_driver"] = rgbl["driver"]
            if rgbl.get("led_count"):
                facts["rgblight_led_count"] = rgbl["led_count"]

        # Backlight
        bl = data.get("backlight")
        if isinstance(bl, dict):
            if bl.get("driver"):
                facts["backlight_driver"] = bl["driver"]
            if bl.get("levels"):
                facts["backlight_levels"] = bl["levels"]

        # Audio
        aud = data.get("audio")
        if isinstance(aud, dict):
            if aud.get("driver"):
                facts["audio_driver"] = aud["driver"]

        # EEPROM
        eep = data.get("eeprom")
        if isinstance(eep, dict):
            if eep.get("driver"):
                facts["eeprom_driver"] = eep["driver"]

        return facts


class QmkCollector:
    """
    Ingests all QMK keyboard targets into the Peripheral Registry staging database.
    """

    def __init__(self, db: RegistryDatabase, repo_path: Path, run_id: str):
        self.db = db
        self.repo_path = repo_path
        self.run_id = run_id
        self.resolver = QmkMetadataResolver(repo_path)
        self.commit_sha = self.resolver.get_commit_sha()
        self.repo_url = "https://github.com/qmk/qmk_firmware"

    def collect(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None,
        manufacturer_filter: Optional[str] = None,
        prefix_filter: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Execute QMK keyboard ingestion across discovered targets.
        """
        targets = self.resolver.list_targets()
        logger.info(f"[QMK] Discovered {len(targets)} keyboard targets in {self.repo_path}")

        # Apply filters
        if prefix_filter:
            p_clean = prefix_filter.strip().lower()
            targets = [t for t in targets if t.lower().startswith(p_clean)]
            logger.info(f"[QMK] Filtered by prefix '{prefix_filter}': {len(targets)} targets")

        if limit and limit > 0:
            targets = targets[:limit]
            logger.info(f"[QMK] Limited to {len(targets)} targets")

        stats = {
            "targets_discovered": len(targets),
            "records_created": 0,
            "records_updated": 0,
            "duplicates": 0,
            "invalid_metadata": 0,
            "skipped_entries": 0,
            "with_vid_pid": 0,
            "without_vid_pid": 0,
            "unique_vid_pids": set(),
            "manufacturers": set(),
            "mcus": set(),
            "bootloaders": set(),
            "features_recorded": 0,
            "hints_recorded": 0,
            "facts_recorded": 0,
        }

        for i, target_path in enumerate(targets, 1):
            try:
                meta = self.resolver.resolve_target(target_path)

                if manufacturer_filter:
                    m_filt = manufacturer_filter.strip().lower()
                    if m_filt not in meta.manufacturer.lower() and m_filt not in meta.display_name.lower():
                        stats["skipped_entries"] += 1
                        continue

                stats["manufacturers"].add(meta.display_name)
                if meta.processor:
                    stats["mcus"].add(meta.processor)
                if meta.bootloader:
                    stats["bootloaders"].add(meta.bootloader)

                if meta.vid and meta.pid:
                    stats["with_vid_pid"] += 1
                    stats["unique_vid_pids"].add(f"{meta.vid_hex}:{meta.pid_hex}")
                else:
                    stats["without_vid_pid"] += 1

                if not dry_run:
                    self._persist_target(meta, stats)
                else:
                    stats["records_created"] += 1

                if i % 500 == 0 or i == len(targets):
                    logger.info(f"[QMK] Progress: {i}/{len(targets)} targets processed...")

            except Exception as e:
                stats["invalid_metadata"] += 1
                logger.error(f"[QMK] Error processing target '{target_path}': {e}", exc_info=True)

        stats["unique_vid_pid_count"] = len(stats["unique_vid_pids"])
        stats["manufacturer_count"] = len(stats["manufacturers"])
        stats["unique_vid_pids"] = list(stats["unique_vid_pids"])
        stats["manufacturers"] = sorted(list(stats["manufacturers"]))
        stats["mcus"] = sorted(list(stats["mcus"]))
        stats["bootloaders"] = sorted(list(stats["bootloaders"]))

        return stats

    def _persist_target(self, meta: QmkTargetMetadata, stats: dict[str, Any]):
        """Persist normalized QMK target facts into SQLite database."""
        # 1. Vendor / Brand
        vendor_id = self.db.get_or_create_vendor(
            name=meta.vendor_slug,
            display_name=meta.display_name,
            website=meta.url
        )

        # 2. Source Provenance
        source_url = f"{self.repo_url}/tree/{self.commit_sha}/keyboards/{meta.target_path}"
        raw_source = RawSource(
            url=source_url,
            source_type=SourceType.OPEN_SOURCE,
            vendor=meta.vendor_slug,
            content_hash=self.commit_sha
        )
        source_id = self.db.record_source(raw_source)

        # 3. Product Normalization & Identity Key
        canonical_name = meta.keyboard_name
        if meta.revision and meta.revision.lower() not in canonical_name.lower():
            canonical_name = f"{canonical_name} {meta.revision.upper()}"

        identity_key = generate_identity_key(meta.display_name, f"{canonical_name}_{meta.target_path}")

        p_id, is_new = self.db.upsert_product(
            vendor_id=vendor_id,
            raw_name=meta.target_path,
            canonical_name=canonical_name,
            category="keyboard",
            identity_key=identity_key,
            product_url=meta.url or source_url,
            image_url=None,
            category_confidence=1.0,
            metadata_confidence=0.75,  # UpstreamDeclared / QmkDeclared
            source_id=source_id,
            evidence_level=EvidenceLevel.LEVEL_1_METADATA,
            run_id=self.run_id
        )

        if is_new:
            stats["records_created"] += 1
        else:
            stats["records_updated"] += 1

        # 4. Device Identifier (VID/PID)
        if meta.vid and meta.pid:
            ident_fact = DeviceIdentifierFact(
                product_id=p_id,
                vid=meta.vid,
                pid=meta.pid,
                vid_hex=meta.vid_hex,
                pid_hex=meta.pid_hex,
                manufacturer_string=meta.manufacturer,
                product_string=meta.keyboard_name,
                usage_page=None,
                usage=None,
                connection_type="usb" if "usb" in meta.connectivity else (meta.connectivity[0] if meta.connectivity else "usb"),
                source_id=source_id,
                artifact_sha256=None,
                evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                confidence=0.75  # QmkDeclared: upstream target declaration
            )
            if self.db.upsert_device_identifier(ident_fact, run_id=self.run_id):
                stats["hints_recorded"] += 1

        # 5. Protocol Hints & Architecture Facts
        # Firmware family
        self.db.upsert_protocol_hint(
            ProtocolHintFact(
                product_id=p_id,
                hint_key="firmware_family",
                hint_value="qmk",
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                confidence=0.95
            ),
            run_id=self.run_id
        )

        # MCU / Processor
        if meta.processor:
            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=p_id,
                    hint_key="mcu",
                    hint_value=meta.processor,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.90
                ),
                run_id=self.run_id
            )

        # Bootloader
        if meta.bootloader:
            self.db.upsert_protocol_hint(
                ProtocolHintFact(
                    product_id=p_id,
                    hint_key="bootloader",
                    hint_value=meta.bootloader,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                    confidence=0.90
                ),
                run_id=self.run_id
            )

        # 6. Technical Facts
        # QMK Target Path & Commit
        self.db.upsert_generic_fact(
            GenericFact(
                product_id=p_id,
                key="qmk_target_path",
                value=meta.target_path,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                confidence=1.0
            ),
            run_id=self.run_id
        )
        self.db.upsert_generic_fact(
            GenericFact(
                product_id=p_id,
                key="qmk_commit",
                value=self.commit_sha,
                source_id=source_id,
                evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                confidence=1.0
            ),
            run_id=self.run_id
        )

        if meta.device_version:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="device_version",
                    value=meta.device_version,
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.85
                ),
                run_id=self.run_id
            )

        if meta.matrix_rows and meta.matrix_cols:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="matrix_dimensions",
                    value=f"{meta.matrix_rows}x{meta.matrix_cols}",
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.95
                ),
                run_id=self.run_id
            )

        if meta.layouts:
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key="layouts",
                    value=", ".join(meta.layouts[:15]),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.95
                ),
                run_id=self.run_id
            )

        # Feature flags
        for f_name, f_val in meta.features.items():
            if f_val:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key=f"feature:{f_name}",
                        value="true",
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                        confidence=0.90
                    ),
                    run_id=self.run_id
                )
                stats["features_recorded"] += 1

        # Hardware facts
        for h_key, h_val in meta.hardware_facts.items():
            self.db.upsert_generic_fact(
                GenericFact(
                    product_id=p_id,
                    key=f"hw:{h_key}",
                    value=str(h_val),
                    source_id=source_id,
                    evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                    confidence=0.90
                ),
                run_id=self.run_id
            )
            stats["facts_recorded"] += 1
