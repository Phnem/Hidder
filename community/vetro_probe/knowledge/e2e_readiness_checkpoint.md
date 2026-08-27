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

## NOT promoted by this run

`keyboard.remap`, `he.actuation`, `he.rt`, `light.rgb_core`, `light.global_color`,
`light.effect`, `light.speed`, `light.direction`, `custom.per_key`,
`light.edge_light`. `K20` is NOT promoted (single-device/single-FW scope).

## Next technical target (prepared, NOT executed)

`he.actuation` — see the recommendation in the run closure notes.

- current blocker: `BLOCKED_PENDING_PHYSICAL_REVALIDATION`
- known previous failure: real run FAILED — baseline 1.63, temp 0.6, readback 0.0,
  rollback 1.63 PASS (off-grid temp value rejected by hardware)
- implemented fix: temporary values chosen from the proven 0.5 mm grid
  `[0.5, 1.0, 1.5, 2.0]` (`bundle_export` actuation bounds)
- exact evidence needed to promote: a real `he.actuation` AUTO_REVERSIBLE run
  whose per-op readback == temp, rollback_readback == baseline, and aggregate
  final verification passes; then close `feature_gates.CLOSED_EVIDENCE[
  "physical Probe PASS after 0.5mm-grid fix"]`.
