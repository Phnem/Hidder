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
  RESOLVED — authoritative real 0x99 READBACK (passive Capture Lab, rt_get_capture.jsonl):
  14 real OUT→IN pairs, all checksums valid, reply layout `[0x99,0,0,1,0,len(8n), record*,
  pad, checksum]`; 8-byte record `key_id_hi,key_id_lo,rt_enable,rt_up_hi,rt_up_lo,
  rt_down_hi,rt_down_lo,rt_global` (matches the real SET capture shape byte-for-byte).
  `parse_rt_get_reply` and `operations.get_rapid_trigger` are IMPLEMENTED; the typed
  `he.rt` GET performs a real 0x99 readback (the quarantined cache is never used).
  Current captured hardware state: 83 keys enable=0, up=down=0.01mm (raw1), global=1;
  key 0x1f enable=0, up=down=0.10mm (raw10), global=1 — the parser handles heterogeneous
  real state.
  Still OPEN: (1) safe temporary grid — the only real SET evidence is the enable toggle
  at 0.10/0.10mm; the RT slider UI label "0.02mm" is display-only, not authoritative
  vendor bounds; (2) a real Probe SET→GET round-trip has not been performed, so
  `rapid_trigger_units_crosscheck` stays PARTIALLY_CLOSED / OPEN_PENDING_ROUNDTRIP and
  `he.rt` remains BLOCKED. RT revalidation is NOT READY until a safe grid is established
  and the reversible round-trip is performed.
- B. `keyboard.remap` — blocker strong E5 / WM_INPUT hDevice observable missing.
  Needs OS-level WM_INPUT correlation infrastructure, independent of the device
  protocol path.

Recommendation: `he.rt` is still the cheaper next target ONCE the 0x99 readback
parser is added from vendor evidence; `keyboard.remap` requires new WM_INPUT
observable infrastructure regardless.

GUI can proceed in parallel: YES — the six-op autonomous set and the full E2E are
closed; RT/remap are independent feature blockers and are not prerequisites for
GUI work on the validated surface.
