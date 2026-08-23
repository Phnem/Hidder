# AULA KB V3 — End-to-End A-Gate: Identity + Remap slice

**Status: CLOSED, accepted 2026-08-23.**

```
HERO84 Identity = PRODUCTION_PHYSICAL_VALIDATED
HERO84 Remap    = PRODUCTION_PHYSICAL_VALIDATED

read_keymap = safe_read
set_remap   = safe_write

FINAL RANK_A = 0/15   (unchanged by this slice, deliberately)
```

Scope, deliberately narrowed 2026-08-23: **only** identity (device UUID →
exact product resolution) and PrtSc remap (`read_keymap`/`set_remap`,
plain-value form). The other 11 planned operations (macro, profiles/device
settings, HE actuation write, RT, deadzone, RGB, polling/reconnect, full
React UI) are **not** in this gate and are not claimed here. See
`Vetro hud/.claude/worktrees/gracious-haslett-7cac7b/docs/decisions/0002-aula-bytech-kb-v3-wired-convergence.md`
for why this work extends the existing `aula-bytech` engine rather than a
new one, and
`.../docs/hardware/aula-bytech-exchange-008-identity-remap-physical.md` for
the full physical record this gate is built on.

## Gate status

| Gate | Status | Evidence |
|---|---|---|
| `BACKEND_READY` | **PASS** | `crates/pcore`/`psafety`/`pproto` in the Peripheral worktree; `read_keymap`/`set_remap` promoted to `safe_read`/`safe_write` in `data/protocols/aula-bytech.toml`; `cargo test --workspace` green (pproto 81/81, psafety 108/108, pcore 24/24, peripheral-app 33/33, tools/emu 25/25) |
| `APP_INTEGRATED` | **PASS** | Real Tauri desktop app (`npm run tauri dev`), Rust IPC commands `read_prtsc_binding`/`set_prtsc_remap` registered in `app/src-tauri/src/lib.rs`, exercised via a temporary `window.prtscDebug` DevTools hook (`app/src/main.tsx`) — no full screen built, per explicit scope |
| `PHYSICAL_E2E_VALIDATED` | **PASS** | HERO 84 HE, device id `925765694`, `modelId` `18691697672197`. Two passes: one against the bootstrap doors (promoted the ACL), one against the production `SafetyGate::read`/`SafetyGate::write` path after promotion forced a refactor — see exchange 008. Two real defects found and fixed by the second pass (missing `record_backup`, a cadence collision from an internal read-before-write) |
| `RANK_A` | **NOT CLAIMED** | Rank A means the full planned operation surface, physically validated, across the catalogued product line. This gate covers 2 of ~13 planned operations on 1 of 15 catalogued products |

## HERO84: PASS (Identity + Remap only)

- Identity: enumeration → `connect_device` → `read_model_id` → exact product
  resolution, all through the production stack, `modelId` resolves
  byte-exact to `"HERO 84 HE"`.
- Remap: baseline read (`0x46`, PrintScreen), write to `0x40`, read-back
  confirms, physical key press independently confirmed as F7 (browser
  key-event decode of HID usage `0x40`), rollback to `0x46`, read-back
  confirms.
- No runtime bug, UI bug, product-data bug, safety bug, or protocol
  contradiction encountered against real hardware in this slice. Two
  implementation bugs were found and fixed *before* physical success (see
  exchange 008) — neither reflects a wrong protocol fact; both were gaps in
  this project's own safety-plumbing wiring, caught by the physical pass
  doing exactly what it exists to do. **Both are now closed as regressions**,
  not just as one-off fixes: `tools/emu/src/aula.rs` gained real
  `read_keymap`/`set_remap` emulator answers for PrtSc (byte-exact against
  `physical_macro_assignment_20260823.jsonl` where captured, checksum
  cross-checked against two more real captures), and six new tests in
  `tools/emu/tests/session_lifetime.rs` name the exact failures:
  `reading_prtsc_is_what_records_the_backup_and_arms_the_write` (the missing
  `record_backup` call) and `a_prtsc_write_needs_exactly_one_cadence_wait_after_its_own_read`
  (the internal-read cadence collision), plus refusal and full-round-trip
  coverage. `cargo test --workspace` green throughout.

## FAMILY BACKEND (aula-bytech): PASS

`data/protocols/aula-bytech.toml` now carries 7 verified commands for this
family (`read_model_id`, `read_key_travel`, `set_key_travel`, `read_keymap`,
`set_remap`, `start_key_travel_monitor`, `stop_key_travel_monitor`), all with
hardware evidence, none on `vendor_artifact` alone. `psafety`'s guardrail
tests (`the_aula_family_has_exactly_the_seven_commands_it_earned`,
`every_write_in_the_registry_is_one_somebody_decided_to_allow`) hold this
list exact rather than open-ended.

## 15 PRODUCT SPECS

| Product | Status |
|---|---|
| HERO 84 HE | **PASS** (Identity + Remap only, physically validated) |
| Other 14 catalogued `aula-bytech` products | **UNVALIDATED** — UUIDs and layouts known from the `DB` tree's `kb_by_v3_wired` research (`aula_bytech_models::EXACT_PRODUCTS`), never physically touched by Peripheral. Emulator-based smoke coverage for these is explicitly deferred, per scope, to after this gate |

**1 / 15 physically validated, and only for 2 of the ~13 planned operations.**

## FINAL RANK_A: N/15 = 0/15

Not claimed, and not close: this gate deliberately covers a 2-operation
vertical slice on a single product to prove the production chain end to end
before widening. Rank A requires the operation surface listed in the
expansion order below, physically validated per product (or defensibly
covered by structural/emulator evidence where physical access is not
possible), across all 15.

## What happens next, in order (per prior agreement — not started)

1. Promote `read_keymap`/`set_remap` is **done** (this gate). Next: macro →
   profiles/device settings → HE actuation/RT/deadzone → RGB →
   polling/reconnect → full React UI, one slice at a time, each ending in a
   physical A-gate the same shape as this one.
2. The old HE-actuation incident (`W 51→75→51` read back as `202/149`) is
   **not** part of this gate and does not block it; it is a separate,
   documented future blocker for the HE actuation write slice specifically.
