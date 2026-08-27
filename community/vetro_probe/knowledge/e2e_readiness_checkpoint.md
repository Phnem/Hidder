# Vetro Probe — Physical E2E Readiness Checkpoint

Status: **core has one real successful autonomous E2E hardware run**
Run: `vetro_auto_physical_k14_full_001` (372E:103E / `aula_kb_v3_wired` / FW 0216)

No formal project percentage is recorded here: there is no canonical stored
metric for "probe completeness" in the repository. This checkpoint records
proven capabilities only.

## Proven (real hardware, additive evidence)

| Capability | Result |
|---|---|
| REAL DEVICE DISCOVERY | PASS |
| EXACT IDENTITY/FW GATING | PASS |
| KNOWLEDGE-AWARE PLANNING | PASS |
| FEATURE-LEVEL SAFETY GATING | PASS |
| AUTO_REVERSIBLE EXECUTION | PASS |
| PER-OP READBACK | PASS |
| PER-OP ROLLBACK | PASS |
| RECOVERY-FIRST ARCHITECTURE | PASS |
| AGGREGATE FINAL VERIFICATION | PASS (authoritative zero-write follow-up) |
| MINER PACKAGE | PASS |
| FULL PHYSICAL E2E | PASS |

## Scoped evidence for the run

- Executed ops = 5, per-op PASS = 5, per-op restored = 5, failed = 0.
- `PER_OP_PHYSICAL_RESULT = 5/5 PASS` (`evidence/*.json`, `certificates/*.vetrojson`).
- The first in-run aggregate reader was defective on real HID
  (`AGGREGATE_READER_DESYNC`, see `aggregate_audit.json`); it is preserved, not
  rewritten. The authoritative final state is the independent zero-write
  verification (`external_readonly_closure.json`, `final_verdict.json`):
  `BASELINE_RESTORED = YES`, `FINAL_STATE_VERIFIED = YES`,
  `CURRENT_HARDWARE_STATE == STORED_INITIAL_BASELINES`, `MANUAL_RESTORE_REQUIRED = NO`,
  `FULL_E2E_PHYSICAL_PASS = YES`.

## CLOSED after the E2E run (dedicated revalidation, not another full --auto)

- `he.actuation` is now **AUTO_REVERSIBLE** (post-fix physical revalidation PASS).
  - baseline 1.63 (raw u16 163, native 0.01 mm) → temporary 1.0 (on-grid) →
    fresh readback 1.0 → immutable restore 1.63 → fresh final GET 1.63 == A →
    STATUS RESTORED (`actuation_revalidation_checkpoint/closure.json`).
  - historical pre-fix failure preserved: A=1.63, B=0.6 (off-grid), readback 0.0,
    rollback 1.63 PASS (evidence of the old temporary-value defect, not rewritten).
  - `feature_gates.CLOSED_EVIDENCE["physical Probe PASS after 0.5mm-grid fix"]` closed.
- Physically validated AUTO_REVERSIBLE set is now **six**:
  `keyboard.profile`, `keyboard.polling`, `device.win_lock`, `he.deadzone`,
  `he.actuation`, `light.brightness`.

## NOT promoted

`keyboard.remap`, `he.rt`, `light.rgb_core`, `light.global_color`,
`light.effect`, `light.speed`, `light.direction`, `custom.per_key`,
`light.edge_light`. `K20` is NOT promoted (single-device/single-FW scope).
No cross-feature inference.

## Next technical blocker (analysis only — not executed)

Compare the two remaining HE/input blockers:

- A. `he.rt` — blocker `rapid_trigger_units_crosscheck` OPEN.
  Resolved statically: raw scale = 0.01 mm (same PRECISION_DISTANCE convention as
  actuation); record (8B) `key_id_hi, key_id_lo, rt_enable, rt_up_hi, rt_up_lo,
  rt_down_hi, rt_down_lo, rt_global`; serializer golden vector is byte-exact vs
  the real live capture `00 1e 01 00 0a 00 0a 01` (enable ON, up=down=0.10 mm);
  rt_enable is a SEPARATE field from the thresholds.
  NOT resolved: the `0x99` GET READBACK path has no parser in this repo
  (`parse_rt_get_reply` / `operations.get_rapid_trigger` absent), and the typed
  transport `he.rt` GET returns a self-confirming cache — so an independent
  "fresh GET B == B" / "final GET A == A" cannot be performed yet. The units
  crosscheck is a REAL-DEVICE SET→GET round-trip and cannot be closed by static
  tests. RT revalidation is therefore NOT READY until an authoritative 0x99
  parser + independent readback exist (see test_vetro_probe_rt_crosscheck.py).
- B. `keyboard.remap` — blocker strong E5 / WM_INPUT hDevice observable missing.
  Needs OS-level WM_INPUT correlation infrastructure, independent of the device
  protocol path.

Recommendation: `he.rt` is still the cheaper next target ONCE the 0x99 readback
parser is added from vendor evidence; `keyboard.remap` requires new WM_INPUT
observable infrastructure regardless.

GUI can proceed in parallel: YES — the six-op autonomous set and the full E2E are
closed; RT/remap are independent feature blockers and are not prerequisites for
GUI work on the validated surface.
