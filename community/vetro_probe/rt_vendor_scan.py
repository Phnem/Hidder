"""Read-only RT slider contract scan of the LOADED vendor JS bundle.

Controller-layer symbols are already in the repository (lt_controller_ops.json:
fetch_rt / sync_rt / parse_rt / kxt), but the Vue UI slider config (min/max/step)
is NOT in the repo. This module scans the bundle resources actually loaded by
https://hero.aulastar.com/ (fetched read-only) and extracts RT-LINKED slider
config candidates with STRICT provenance (VENDOR_BUNDLE) and linkage (PROVEN only
when an RT anchor co-occurs with a numeric min/max/step/precision config).

Rules:
- Only RT-anchored config is promoted (fetch_rt / sync_rt / kxt / _transfer_rt /
  rt_enable / rt_up / rt_down / rapid / trigger).
- Actuation config (anchored near fetch_distance / parse_distance) is NEVER reused
  as RT evidence.
- The protocol storage quantum (0.01 mm) is never substituted for a UI step.
- If no provable grid is found, SAFE RT MUTATION CONTRACT stays NOT_PROVEN and
  select_temporary_rt_threshold() raises (fail-closed).
- This module performs ZERO HID writes and never executes any handler.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Callable

RT_ANCHORS = ("fetch_rt", "sync_rt", "kxt", "_transfer_rt", "rt_enable", "rt_up",
              "rt_down", "rapid", "trigger")
ACTUATION_ANCHORS = ("fetch_distance", "parse_distance", "sync_distance", "actuation")
CONFIG_PATTERNS = [
    re.compile(r'step\s*[:=]\s*"?([0-9]*\.?[0-9]+)"?'),
    re.compile(r'min\s*[:=]\s*"?([0-9]*\.?[0-9]+)"?'),
    re.compile(r'max\s*[:=]\s*"?([0-9]*\.?[0-9]+)"?'),
    re.compile(r'precision\s*[:=]\s*"?([0-9]+)"?'),
    re.compile(r'\[([0-9]*\.?[0-9]+(?:,\s*[0-9]*\.?[0-9]+)*)\]'),
]

WINDOW = 400  # chars around an anchor to look for config


def _has_rt_context(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - WINDOW):min(len(text), end + WINDOW)]
    hits = [a for a in RT_ANCHORS if a in window]
    act = [a for a in ACTUATION_ANCHORS if a in window]
    # RT anchor present AND no stronger actuation-only context
    return bool(hits) and not (act and not any(a in window for a in ("rt_up", "rt_down", "rt_enable", "sync_rt", "fetch_rt")))


def _extract_config(text: str) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    for pat in CONFIG_PATTERNS:
        for m in pat.finditer(text):
            v = m.group(1)
            try:
                num = float(v)
            except ValueError:
                continue
            key = None
            for kw in ("step", "min", "max", "precision"):
                if kw in m.group(0):
                    key = kw
                    break
            if key and key not in cfg:
                cfg[key] = num
            # generic array literal -> candidate grid
            if key is None and "," in v:
                cfg.setdefault("grid_candidates", []).append(v)
    return cfg


def scan_rt_slider_contract(script_urls: list[str], fetch_fn: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Scan loaded bundle scripts read-only for RT-linked slider config."""
    fetch_fn = fetch_fn or (lambda url: urllib.request.urlopen(url, timeout=15).read().decode("utf-8", errors="replace"))
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    for url in script_urls:
        try:
            text = fetch_fn(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc!r}")
            continue
        for anchor in RT_ANCHORS:
            for m in re.finditer(re.escape(anchor), text):
                if not _has_rt_context(text, m.start(), m.end()):
                    continue
                cfg = _extract_config(text[max(0, m.start() - WINDOW):m.end() + WINDOW])
                if cfg:
                    candidates.append({
                        "anchor": anchor, "url": url,
                        "linkage": "PROVEN", "provenance": "VENDOR_BUNDLE", "config": cfg,
                    })
    # dedupe identical (url, config)
    seen = set()
    deduped = []
    for c in candidates:
        k = (c["url"], json.dumps(c["config"], sort_keys=True))
        if k in seen:
            continue
        seen.add(k)
        deduped.append(c)
    proven = [c for c in deduped if c["linkage"] == "PROVEN" and c["config"].get("min") is not None
              and c["config"].get("max") is not None and c["config"].get("step") is not None]
    return {
        "source": "vendor_bundle_scan",
        "resources_scanned": len(script_urls),
        "errors": errors,
        "candidates": deduped,
        "proven": proven,
        "safe_rt_mutation_contract": "PROVEN" if proven else "NOT_PROVEN",
    }


def select_temporary_rt_threshold(A_mm: float, contract: dict[str, Any]) -> tuple[float, dict]:
    """Deterministic safe temporary RT threshold B from a PROVEN vendor grid.

    B on the vendor step grid, within [min,max], clearly different from A,
    minimal reasonable movement, preserving rt_enable/rt_global/untouched field
    (enforced by the caller's record construction). RAISES if the contract is
    not PROVEN. Baseline A is never normalized."""
    proven = contract.get("proven") or []
    if not proven:
        raise RuntimeError(
            "SAFE RT MUTATION CONTRACT NOT_PROVEN — cannot select a threshold B "
            "without RT-linked vendor min/max/step evidence")
    cfg = proven[0]["config"]
    lo, hi, step = float(cfg["min"]), float(cfg["max"]), float(cfg["step"])
    if not (lo < hi and step > 0):
        raise RuntimeError("invalid proven RT grid")
    # candidate grid within [lo, hi]
    vals = []
    v = lo
    while v <= hi + 1e-9:
        vals.append(round(v, 4))
        v += step
    far = [g for g in vals if abs(g - A_mm) >= max(step, 0.05)]
    if not far:
        far = [g for g in vals if abs(g - A_mm) > 0]
    if not far:
        raise RuntimeError("no grid value differs from baseline")
    B = min(far, key=lambda g: abs(g - A_mm))
    return B, {"grid": vals, "baseline_mm": A_mm, "chosen_mm": B,
               "source": "vendor_bundle_scan", "min_mm": lo, "max_mm": hi, "step_mm": step}
