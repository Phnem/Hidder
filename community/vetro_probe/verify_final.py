"""Read-only aggregate final verification for a completed physical auto run.

ZERO writes. Opens a fresh read session PER baseline op (with a settle delay),
GETs the op's current value, and compares byte-for-byte against the initial
baselines stored in <run_dir>/baselines/baselines.json.

Purpose: independently confirm the device is back at the initial baselines after
a full auto run — especially when the in-run aggregate reader was desynced on
real HID (a burst of GETs on one session can consume stale replies; observed on
real HERO84: polling read as 17, win_lock as true, brightness as a 1-byte
frame). This command never mutates state.
"""

from __future__ import annotations

import time
from pathlib import Path


def run_readonly_verify(run_dir: Path, bundle=None) -> dict:
    import json

    from .bundle import production_bundle_for_hero84
    from .identity import ExactIdentityGate, discover_real_instance_via_raw
    from .aula_transport import AulaHidTransport

    run_dir = Path(run_dir)
    blobs = json.loads((run_dir / "baselines" / "baselines.json").read_text(encoding="utf-8"))
    bundle = bundle or production_bundle_for_hero84()
    uuid = int(bundle.product.uuid)

    transport = AulaHidTransport.open_real(uuid=uuid)
    instance = discover_real_instance_via_raw(transport.raw)
    verdict = ExactIdentityGate(bundle).evaluate(instance)
    if not verdict.passed:
        print("READONLY_VERIFY = BLOCKED (identity/FW mismatch)")
        print(f"  {verdict.reason}")
        print("WRITES = 0")
        return {"ok": False, "writes": 0, "blocked": verdict.reason}

    results: dict[str, dict] = {}
    ok_all = True
    for op, baseline in blobs.items():
        try:
            fresh = AulaHidTransport.open_real(uuid=uuid)
        except Exception as exc:  # noqa: BLE001
            results[op] = {"expected": baseline, "actual": None, "matched": False, "error": f"open: {exc!r}"}
            ok_all = False
            continue
        try:
            time.sleep(0.15)
            val, res = fresh.get(op)
        except Exception as exc:  # noqa: BLE001
            results[op] = {"expected": baseline, "actual": None, "matched": False, "error": f"get: {exc!r}"}
            ok_all = False
            fresh.invalidate()
            continue
        try:
            fresh.invalidate()
        except Exception:
            pass
        if not res.ok:
            results[op] = {"expected": baseline, "actual": None, "matched": False, "error": res.error}
            ok_all = False
            continue
        matched = val == baseline
        results[op] = {"expected": baseline, "actual": val, "matched": matched, "error": ""}
        if not matched:
            ok_all = False

    print("=== READ-ONLY AGGREGATE FINAL VERIFICATION (ZERO writes) ===")
    for op, r in results.items():
        mark = "PASS" if r["matched"] else "FAIL"
        extra = f"  err={r['error']}" if r.get("error") else ""
        print(f"  [{mark}] {op}: expected={r['expected']!r} actual={r['actual']!r}{extra}")
    print(f"READONLY_VERIFY = {'VERIFIED' if ok_all else 'UNVERIFIED'}")
    print("WRITES = 0")
    return {"ok": ok_all, "results": results, "writes": 0}
