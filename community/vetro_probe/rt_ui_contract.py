"""RT threshold UI-contract knowledge (from authoritative repository evidence).

CONFIRMED (vendor source `lt_controller_ops.json` + real hardware 0x99 capture):
- RT record (8B): key_id_hi, key_id_lo, rt_enable, rt_up_hi, rt_up_lo,
  rt_down_hi, rt_down_lo, rt_global. Vendor `parse_rt` decodes exactly as our
  parse_rt_get_reply (pos = rec[0]*256+rec[1], rt_enable = rec[2],
  rt_up = rec[3]*256+rec[4], rt_down = rec[5]*256+rec[6], rt_global = rec[7]).
- GET decode (`fetch_rt`): rt_up_mm = raw * precision_distance,
  rt_down_mm = raw * precision_distance -> UP AND DOWN SHARE THE SAME CONTRACT.
  precision_distance scaling is fixed by real hardware (raw 1 = 0.01 mm,
  raw 10 = 0.10 mm) => semantic scale = 0.01 mm/raw.
- SET (`sync_rt` + `kxt`): group 0x19, payload[5] = record byte length, records
  built per key by `_transfer_rt`; matches our build_rt_set_frame.

NOT established (NOT in repository): the vendor UI SLIDER component config for RT
threshold values (min / max / step / snapping). The RT-page DOM snapshot shows a
"distance 0.02mm" label, but that is display-only and NOT tied to the control's
step in any code we hold. Native 0.01 mm quantum is NOT a vendor-safe selection
grid. Therefore the safe temporary RT grid is OPEN and no temporary B may be
fabricated.
"""

RT_THRESHOLD_SCALE_MM = 0.01  # raw->mm (vendor-source + real hardware confirmed)
UP_DOWN_SAME_CONTRACT = True  # vendor fetch_rt applies identical decode to up/down

SAFE_TEMP_GRID = "OPEN"  # authoritative UI min/max/step NOT established

_MISSING_EVIDENCE = (
    "authoritative vendor RT slider min/max/step/snapping (Vue component config, "
    "not present in repository; the '0.02mm' DOM label is display-only and unproven); "
    "a real Probe SET->GET round-trip to close rapid_trigger_units_crosscheck"
)


def select_temporary_threshold(baseline_mm: float) -> tuple[float, dict]:
    """Deterministic safe temporary RT threshold.

    RAISES until the vendor UI selection contract is authoritatively established:
    the native 0.01 mm quantum and the two observed hardware values (0.01 / 0.10 mm)
    do NOT define a safe selection grid. No B is fabricated from insufficient
    evidence."""
    raise RuntimeError(
        "safe temporary RT grid is OPEN — cannot select a threshold B "
        f"(baseline {baseline_mm!r}) without authoritative vendor UI min/max/step; "
        f"missing evidence: {_MISSING_EVIDENCE}"
    )


def contract_summary() -> dict:
    return {
        "record": ["key_id_hi", "key_id_lo", "rt_enable", "rt_up_hi", "rt_up_lo",
                   "rt_down_hi", "rt_down_lo", "rt_global"],
        "native_quantum_mm": RT_THRESHOLD_SCALE_MM,
        "up_down_same_contract": UP_DOWN_SAME_CONTRACT,
        "safe_temp_grid": SAFE_TEMP_GRID,
        "missing_evidence": _MISSING_EVIDENCE,
        "observed_hardware_values_mm": [0.01, 0.10],
    }
