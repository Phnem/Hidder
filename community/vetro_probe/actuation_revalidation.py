"""Dedicated physical revalidation for he.actuation (post 0.5mm-grid fix).

NOT full --auto: one controlled, reversible experiment on one key position.
Reuses the proven write-ahead/recovery model (RunStateStore) and the exact
identity/FW gate (372E:103E / aula_kb_v3_wired / exact 0216).

Baseline semantics (authoritative, from protocol.py):
  raw unit = 0.01 mm (PRECISION_DISTANCE=10); mm_to_raw = round(mm*100),
  raw_to_mm = raw/100.0. So a baseline like 1.63 is a LEGITIMATE device-reported
  value (u16 raw 163) with finer native precision than the vendor-UI 0.5 mm
  selection grid. The immutable baseline is ALWAYS restored exactly (raw u16) —
  never normalized to the grid. The 0.5 mm grid constrains only temporary B,
  matching the fix for the prior real failure (temp 0.6 off-grid -> readback 0.0,
  rollback restored 1.63).
"""

from __future__ import annotations

import time
from pathlib import Path

# The proven safe selection grid (ACTUATION_GRIDLINE_MM = 0.5).
GRID = (0.5, 1.0, 1.5, 2.0)
# Minimum |B - A| so the readback is unambiguous vs the baseline.
MIN_DELTA_MM = 0.3
# Central grid values preferred over the extremes when both are available.
_CENTRAL = (1.0, 1.5)

UNCERTAIN_STATES = ("TEMP_WRITE_INTENT", "TEMP_WRITE_APPLIED", "RESTORE_INTENT")
OPERATION = "he.actuation.revalidation"


def plan_actuation_temporary(A_mm: float) -> tuple[float, dict]:
    """Deterministic safe temporary B selection from the proven grid.

    Rule:
      1. candidates = grid values with |B - A| >= 0.3 mm (unambiguous readback);
         if none, candidates = grid values != A.
      2. prefer a central grid value (1.0 / 1.5) when one is available and its
         extra movement over the nearest candidate is <= 0.5 mm (avoid the
         extremes unless a central value would be a much larger move).
      3. otherwise choose the nearest candidate (smallest |B - A|).
    B is always on-grid, != A, and inside the proven 0.5..2.0 range. Never
    returns the extreme unless it is the only clearly-different option.
    """
    if not isinstance(A_mm, (int, float)):
        raise ValueError(f"baseline must be mm numeric, got {A_mm!r}")
    far = [g for g in GRID if abs(g - A_mm) >= MIN_DELTA_MM]
    if not far:
        far = [g for g in GRID if g != A_mm]
    if not far:
        raise ValueError(f"no grid value differs from baseline {A_mm!r}")
    nearest = min(far, key=lambda g: abs(g - A_mm))
    central = [g for g in far if g in _CENTRAL]
    if central:
        best_central = min(central, key=lambda g: abs(g - A_mm))
        if abs(best_central - A_mm) - abs(nearest - A_mm) <= 0.5:
            chosen = best_central
        else:
            chosen = nearest
    else:
        chosen = nearest
    return chosen, {
        "grid": list(GRID), "baseline_mm": A_mm, "chosen_mm": chosen,
        "rule": "nearest clearly-different grid value, preferring a central value "
                "when the extra movement is <=0.5mm",
    }


def _restore_actuation(A_mm: float, make_transport) -> dict:
    """Restore the immutable original baseline A (exact mm -> exact raw u16)."""
    out = {"stage": "restore_A", "written": A_mm,
           "restore_write_issued": False, "restore_write_completed": False,
           "final_get_observed": False, "final_get": None, "final_get_equals_A": False,
           "ok": False, "error_code": "", "error": ""}
    try:
        s = make_transport()
    except Exception as exc:  # noqa: BLE001
        out["error_code"] = "OPEN_FAILED"
        out["error"] = f"open: {exc!r}"
        return out
    rw = s.set("he.actuation", A_mm)
    out["restore_write_issued"] = True
    out["restore_write_completed"] = rw.ok
    try:
        s.close() if hasattr(s, "close") else s.invalidate()
    except Exception:
        pass
    if not rw.ok:
        out["error_code"] = "SET_A_FAILED"
        out["error"] = rw.error
        return out
    time.sleep(0.1)
    try:
        s2 = make_transport()
    except Exception as exc:  # noqa: BLE001
        out["error_code"] = "OPEN_FAILED"
        out["error"] = f"reopen for final GET: {exc!r}"
        return out
    fa, r2 = s2.get("he.actuation")
    try:
        s2.close() if hasattr(s2, "close") else s2.invalidate()
    except Exception:
        pass
    out["final_get_observed"] = r2.ok and fa is not None
    out["final_get"] = fa
    out["final_get_equals_A"] = bool(r2.ok and fa == A_mm)
    out["ok"] = out["final_get_equals_A"]
    if not out["ok"]:
        if not out["final_get_observed"]:
            out["error_code"] = "GET_A_FAILED"
            out["error"] = f"final GET failed: {r2.error}"
        else:
            out["error_code"] = "FINAL_STATE_MISMATCH"
            out["error"] = f"final {fa!r} != immutable baseline {A_mm!r}"
    return out


def run_actuation_revalidation(make_transport, A_mm: float, on_phase=None) -> dict:
    """GET A -> durable TEMP_WRITE_INTENT -> SET B -> fresh GET B == B ->
    durable RESTORE_INTENT -> SET immutable A -> fresh final GET A == A -> RESTORED.

    Every GET/set uses a fresh session (never reuse a stale handle). on_phase
    fires BEFORE the physical write of each intent so the caller can persist it
    durably. Failure taxonomy: OPEN_FAILED / SET_B_FAILED / READBACK_B_MISMATCH /
    GET_B_FAILED / SET_A_FAILED / GET_A_FAILED / FINAL_STATE_MISMATCH.
    """
    stages: list[dict] = []
    res: dict = {"ok": False, "stages": stages, "recovered": False}

    if on_phase:
        on_phase("BASELINE_SAVED")
    B, plan = plan_actuation_temporary(A_mm)
    res["temporary_B"] = B
    res["temporary_plan"] = plan

    # ---- SET B ----
    if on_phase:
        on_phase("TEMP_WRITE_INTENT")
    try:
        s1 = make_transport()
    except Exception as exc:  # noqa: BLE001
        res["error_code"] = "OPEN_FAILED"
        res["error"] = f"open: {exc!r}"
        return res
    wr = s1.set("he.actuation", B)
    stages.append({"stage": "write_B", "written": B, "set_ok": wr.ok, "error": wr.error})
    try:
        s1.close() if hasattr(s1, "close") else s1.invalidate()
    except Exception:
        pass
    if not wr.ok:
        res["error_code"] = "SET_B_FAILED"
        res["error"] = wr.error
        return res
    if on_phase:
        on_phase("TEMP_WRITE_APPLIED")

    # ---- fresh GET B (readback, not ACK) ----
    time.sleep(0.1)
    try:
        s2 = make_transport()
    except Exception as exc:  # noqa: BLE001
        res["error_code"] = "OPEN_FAILED"
        res["error"] = f"reopen for GET B: {exc!r}"
        if on_phase:
            on_phase("RESTORE_INTENT")
        rec = _restore_actuation(A_mm, make_transport)
        stages.append(rec)
        res["recovered"] = rec["ok"]
        res["recovery_code"] = rec.get("error_code", "")
        if on_phase:
            on_phase("RESTORED" if rec["ok"] else "RESTORE_INTENT")
        return res
    rb, r2 = s2.get("he.actuation")
    try:
        s2.close() if hasattr(s2, "close") else s2.invalidate()
    except Exception:
        pass
    stages.append({"stage": "readback_B", "got": rb, "expected": B, "match": rb == B,
                   "get_ok": r2.ok, "error": r2.error})
    if not r2.ok or rb != B:
        if not r2.ok:
            res["error_code"] = "GET_B_FAILED"
            res["error"] = f"fresh GET B failed: {r2.error}"
        else:
            res["error_code"] = "READBACK_B_MISMATCH"
            res["error"] = f"fresh GET B {rb!r} != temporary {B!r}"
        if on_phase:
            on_phase("RESTORE_INTENT")
        rec = _restore_actuation(A_mm, make_transport)
        stages.append(rec)
        res["recovered"] = rec["ok"]
        res["recovery_code"] = rec.get("error_code", "")
        if on_phase:
            on_phase("RESTORED" if rec["ok"] else "TEMP_WRITE_APPLIED")
        return res

    # ---- restore immutable A ----
    if on_phase:
        on_phase("RESTORE_INTENT")
    rec = _restore_actuation(A_mm, make_transport)
    stages.append(rec)
    res["recovered"] = rec["ok"]
    res["ok"] = rec["ok"]
    if on_phase:
        on_phase("RESTORED" if rec["ok"] else "RESTORE_INTENT")
    return res


def revalidation_identity_ok(bundle, instance) -> tuple[bool, str]:
    """Exact hardware gate for the real actuation write: ZERO WRITES on mismatch."""
    b_vid = str(bundle.product.vid).lower()
    b_pid = str(bundle.product.pid).lower()
    if str(instance.vid).lower() != b_vid or str(instance.pid).lower() != b_pid:
        return False, f"VID/PID mismatch: {instance.vid}:{instance.pid} != {bundle.product.vid}:{bundle.product.pid}"
    if bundle.family != "aula_kb_v3_wired":
        return False, f"family mismatch: {bundle.family} != aula_kb_v3_wired"
    fw = str(getattr(instance, "firmware_version", "") or "")
    if fw != "0216":
        return False, f"firmware mismatch: {fw!r} != exact '0216'"
    return True, "exact 372E:103E / aula_kb_v3_wired / FW 0216"


def run_cli_revalidation(out_dir: Path, bundle=None) -> int:
    """CLI entry for --actuation-revalidation (real hardware, ZERO writes on gate fail)."""
    import sys

    from .bundle import production_bundle_for_hero84
    from .identity import discover_real_instance_via_raw
    from .aula_transport import AulaHidTransport
    from .runstate import RunStateStore, RunCheckpoint

    bundle = bundle or production_bundle_for_hero84()
    store = RunStateStore(out_dir)
    cp = store.load()

    print("RECOVERY_PREFLIGHT", end=" = ")
    if cp is not None and not cp.closed and getattr(cp, "operation", "") == OPERATION \
            and cp.phase in UNCERTAIN_STATES:
        print("RECOVERING (pending he.actuation revalidation detected)")
        A = float(cp.baseline)
        rec = _restore_actuation(A, lambda: AulaHidTransport.open_real(uuid=int(bundle.product.uuid)))
        if rec["ok"]:
            cp.closed = True
            cp.phase = "RESTORED"
            cp.recovery_required = False
            store.save(cp)
            print(f"RECOVERY_RESTORED baseline {A!r}")
        else:
            cp.recovery_required = True
            cp.closed = False
            store.save(cp)
            print(f"RECOVERY_BLOCKED: {rec.get('error_code')} {rec.get('error')}", file=sys.stderr)
            print("FAILED_REQUIRES_MANUAL_RESTORE")
            return 2
    else:
        print("CLEAR")

    transport = AulaHidTransport.open_real(uuid=int(bundle.product.uuid))
    instance = discover_real_instance_via_raw(transport.raw)
    ok_gate, reason = revalidation_identity_ok(bundle, instance)
    if not ok_gate:
        print(f"BLOCKED (ZERO WRITES): {reason}")
        return 2

    # fresh authoritative baseline
    val, res = transport.get("he.actuation")
    transport.invalidate()
    if not res.ok or val is None:
        print(f"BLOCKED (ZERO WRITES): baseline GET failed: {res.error}")
        return 2
    A = float(val)
    print(f"BASELINE_GET = {A!r}  (raw {round(A * 100)})")

    if cp is None:
        cp = store.new_run()
    cp.operation = OPERATION
    cp.baseline = A
    cp.device = {"vid": "0x372E", "pid": "0x103E", "family": "aula_kb_v3_wired", "firmware": "0216"}

    def on_phase(phase: str) -> None:
        cp.phase = phase
        cp.closed = phase == "RESTORED"
        if phase == "RESTORED":
            cp.final_verified = True
            cp.recovery_required = False
        store.save(cp)  # durable before the next physical step

    result = run_actuation_revalidation(
        lambda: AulaHidTransport.open_real(uuid=int(bundle.product.uuid)), A, on_phase=on_phase)

    for st in result["stages"]:
        print(f"  [{st.get('stage')}] {st}")

    if result["ok"]:
        print(f"\nhe.actuation revalidation = PASS (baseline {A!r} restored byte-for-byte)")
        print("STATUS = RESTORED")
        return 0
    restored = result.get("recovered")
    print(f"\nhe.actuation revalidation = {'RECOVERED' if restored else 'FAILED — manual restore required'}"
          f"\n  reason: {result.get('error_code')}   detail: {result.get('error')}")
    return 0 if restored else 2
