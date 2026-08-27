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

import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Callable

THRESHOLD_ANCHORS = ("rt_up", "rt_down", "_transfer_rt", "sync_rt", "kxt", "fetch_rt")
CONTEXT_ANCHORS = ("rt_enable", "rapid", "trigger")
ALL_RT_ANCHORS = THRESHOLD_ANCHORS + CONTEXT_ANCHORS
ACTUATION_ANCHORS = ("fetch_distance", "parse_distance", "sync_distance", "actuation")
CONFIG_PATTERNS = [
    re.compile(r'step\s*[:=]\s*"?([0-9]*\.?[0-9]+)"?'),
    re.compile(r'min\s*[:=]\s*"?([0-9]*\.?[0-9]+)"?'),
    re.compile(r'max\s*[:=]\s*"?([0-9]*\.?[0-9]+)"?'),
    re.compile(r'precision\s*[:=]\s*"?([0-9]+)"?'),
    re.compile(r'\[([0-9]*\.?[0-9]+(?:,\s*[0-9]*\.?[0-9]+)*)\]'),
]

WINDOW = 400  # chars around an anchor to look for config


def _window_linkage(text: str, start: int, end: int) -> str:
    """Classify the config's linkage from the anchor window.

    A threshold anchor (rt_up / rt_down / sync_rt / fetch_rt / kxt / _transfer_rt)
    in the window makes the config a THRESHOLD_CANDIDATE. rt_enable / rapid /
    trigger alone make it only RT_CONTEXT_ONLY — which can NEVER prove a
    threshold grid (the user's acceptance rule). Lexical proximity is evidence,
    not dataflow proof: the safe contract stays NOT_PROVEN unless the caller
    explicitly confirms dataflow (dataflow_confirmed=True)."""
    window = text[max(0, start - WINDOW):min(len(text), end + WINDOW)]
    thr = [a for a in THRESHOLD_ANCHORS if a in window]
    ctx = [a for a in CONTEXT_ANCHORS if a in window]
    if thr:
        return "THRESHOLD_CANDIDATE"
    if ctx:
        return "RT_CONTEXT_ONLY"
    return "UNRELATED"


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


def scan_rt_slider_contract(script_urls: list[str], fetch_fn: Callable[[str], str] | None = None,
                            dataflow_confirmed: bool = False, save_dir: Path | None = None) -> dict[str, Any]:
    """Scan loaded bundle scripts read-only for RT slider config evidence.

    dataflow_confirmed=False by default: lexical proximity is evidence, NOT proof.
    The safe contract is PROVEN only when (a) a THRESHOLD anchor co-occurs with
    min/max/step AND (b) the caller explicitly confirms dataflow (rt_up/rt_down
    <-> slider model). rt_enable/rapid/trigger anchoring alone can never prove a
    threshold grid.

    save_dir: if set, the EXACT fetched chunk text is persisted as
    <save_dir>/<sha256>.js and each resource is recorded with url + sha256 + byte
    length so the exact evidence is retained for a later intentional dataflow
    proof (never silently substituted by another bundle)."""
    fetch_fn = fetch_fn or (lambda url: urllib.request.urlopen(url, timeout=15).read().decode("utf-8", errors="replace"))
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    resources: list[dict[str, Any]] = []
    if save_dir is not None:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
    for url in script_urls:
        try:
            text = fetch_fn(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc!r}")
            continue
        raw = text.encode("utf-8", errors="replace")
        sha = hashlib.sha256(raw).hexdigest()
        res_rec: dict[str, Any] = {"url": url, "sha256": sha, "bytes": len(raw)}
        if save_dir is not None:
            dest = Path(save_dir) / f"{sha}.js"
            dest.write_bytes(raw)
            res_rec["saved_to"] = str(dest)
        resources.append(res_rec)
        for anchor in ALL_RT_ANCHORS:
            for m in re.finditer(re.escape(anchor), text):
                linkage = _window_linkage(text, m.start(), m.end())
                if linkage == "UNRELATED":
                    continue
                cfg = _extract_config(text[max(0, m.start() - WINDOW):m.end() + WINDOW])
                if cfg:
                    reading = None
                    if cfg.get("min") is not None and cfg.get("max") is not None and cfg.get("step") is not None:
                        reading = {
                            "provisional_if_raw_0_01_mm": {
                                "min_mm": cfg["min"] * 0.01,
                                "max_mm": cfg["max"] * 0.01,
                                "step_mm": cfg["step"] * 0.01,
                            },
                            "units_unconfirmed": True,
                        }
                    candidates.append({
                        "anchor": anchor, "url": url, "sha256": sha,
                        "linkage": linkage, "provenance": "VENDOR_BUNDLE", "config": cfg,
                        "raw_unit_reading": reading,
                        "dataflow_confirmed": False,
                    })
    # dedupe identical (url, config, linkage)
    seen = set()
    deduped = []
    for c in candidates:
        k = (c["url"], c["linkage"], json.dumps(c["config"], sort_keys=True))
        if k in seen:
            continue
        seen.add(k)
        deduped.append(c)
    proven = [c for c in deduped if c["linkage"] == "THRESHOLD_CANDIDATE" and dataflow_confirmed
              and c["config"].get("min") is not None and c["config"].get("max") is not None
              and c["config"].get("step") is not None]
    for c in proven:
        c["dataflow_confirmed"] = True
    has_threshold = any(c["linkage"] == "THRESHOLD_CANDIDATE" for c in deduped)
    return {
        "source": "vendor_bundle_scan",
        "resources_scanned": len(script_urls),
        "resources": resources,
        "errors": errors,
        "candidates": deduped,
        "proven": proven,
        "has_threshold_candidate": has_threshold,
        "dataflow_linkage": "PROVEN" if proven else ("THRESHOLD_CANDIDATE" if has_threshold else "NOT_PROVEN"),
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
