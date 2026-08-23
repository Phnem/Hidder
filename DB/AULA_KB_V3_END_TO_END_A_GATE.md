# AULA KB V3 — End-to-End A-Gate

**Last recomputed 2026-08-24.** The rank below is not stored anywhere; it is
computed by `crates/pproto/src/aula_bytech_rank.rs` from the ACL's own command
classes and the generated product catalogue, and printed by
`cargo run -p pproto --example rank_matrix`. Nothing in this document can be
raised without changing what is known.

```
FINAL RANK_A = 0/15
```

## What is closed, and what closed it

| Capability | State | How |
|---|---|---|
| Identity | **CLOSED** | `read_model_id` `safe_read`. Exact product resolution through the catalogue, not through VID/PID — 14 of the 15 rows share `0x372E:0x103E`. |
| Remap | **CLOSED** | `read_keymap` `safe_read`, `set_remap` `safe_write`. Two physical passes, 2026-08-23 (exchange 008). |
| Macro | **CLOSED** | `set_macro_table` `safe_write`, assignment through `set_remap`. Two physical passes, 2026-08-23 (exchange 009), including witnessed playback. |
| HE actuation | **CLOSED**, with a caveat | `read_key_travel` `safe_read`, `set_key_travel` `slow_flash`. Physically validated 2026-08-19 (exchanges 005/006). See the open incident below. |
| Profiles | candidate | `read_profile_index` / `set_profile_index` at the bootstrap doors. |
| Polling | candidate | `read_polling_rate` / `set_polling_rate` at the doors. Transaction contract written and tested (`psafety::reconnect`); never exercised on hardware from here. |
| Device settings | candidate | Four registers, both directions, at the doors. |
| RGB | candidate | `read_light_mode` / `set_light_mode` at the doors. The weakest-evidenced entry in the ACL — no capture of a `0x04:0x01` write exists on either research path. |
| HE rapid trigger | candidate | `read_rapid_trigger` / `set_rapid_trigger` at the doors. |
| HE dead zone | candidate | `read_deadzone` / `set_deadzone` at the doors. |
| Per-key HE | candidate | Closed exactly when the three analog writes are; open while any is not. |

"Candidate" means typed, tested against the emulator, and holding no
`SafeCommandId`. Nothing on a product surface can reach one, by construction.

## The bootstrap doors, 2026-08-24

Sixteen reads and nine writes are waiting for a first exchange. That is the
largest batch this family's ACL has ever taken at once, and it is deliberate:
the surface was ported in one pass so that one physical session can close as
much of it as the evidence allows.

Empty is still the doors' resting state. `psafety`'s own guardrails
(`exactly_the_planned_batch_is_awaiting_a_first_exchange`,
`the_door_holds_exactly_the_commands_that_are_mid_promotion`) hold the contents
exact rather than merely non-empty, so a command appearing that nobody put there
fails the build.

## Never generated, and staying that way

`reset_group` (0x11), `start_full_calibration` (0x94) and
`remove_advanced_key` (0x12) are classified `destructive` in the ACL. The
generator emits no command id of any kind for that class, so there is no value
in the program that names them, through any door.

Group 0x11 has an entry specifically because `payload[3]` — not the subcommand —
decides between a layer reset and a **factory reset**, and every other write in
this family carries a constant `1` there. Anything that treats byte 3 as
boilerplate and reuses a template eventually emits a factory reset.

`set_auto_calibrate` (register 25) is **not** the calibration procedure. It is a
one-byte flag, reversible in one write. The two share a word in their names and
nothing else, and both ACL entries say so.

## Open contradictions

Two disagreements between this project's own two research paths block every
product row, which is deliberate: a rank that came out clean while they stood
would be the wrong rank. Both are written up with the frames on all sides in
`Vetro hud/.claude/worktrees/.../docs/hardware/aula-bytech-request-shape-contradictions.md`.

1. **Byte 5 of an actuation record: pad byte, or per-key scope flag.** Three
   independent observations — the vendor's single-key frames, the vendor's
   whole-board sweeps, and this board's own read replies — agree that `01`
   means "this key has its own value" and `00` means "it follows the board's".
   Peripheral writes `00`. This is now the leading explanation for the
   unresolved `W 51→75→51 → 202/149` incident, and there is a three-step test
   that settles it without waiting for the failure to recur.

2. **The profile-name "unnamed" sentinel.** One account says a length byte of
   `0xFF`; the only captured reply carries length `0x38` and fifty-six `0xFF`
   bytes. The decoder recognises both and records which arrived rather than
   picking one.

Two further disagreements were found in the same pass and are settled by
capture: the `0x84` request length byte is not uniform across registers, and
register 1 is seven bytes rather than three.

## The 15 products

Every row: family semantics established on hardware (once, on the reference
unit), and the vendor's own per-product layout data. One row has anything more.

| Product | Model id | Validation states | Blockers |
|---|---|---|---|
| HERO 84 HE | 18691697672197 | FAMILY_HARDWARE, PRODUCT_BINDING, PRODUCT_LAYOUT, PRODUCT_CAPS | 12 |
| HERO 68 HE | 18691697672195 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| HERO 68 AIR | 18691697672212 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| WIN68HE Ultra | 18691697672213 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| HERO 68 MINI | 18691697672207 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| HERO 68 HE wireless | 19791209299969 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 15 |
| HERO 68 HE wireless | 21990232555523 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 15 |
| HERO 99 | 18691697672210 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| HERO 87 HE | 18691697672214 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| HERO 68 HE NEO | 18691697672218 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| HERO 68 HE JIS | 18691697672232 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| HERO 100 | 18691697672216 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 15 |
| HERO 99 HE | 18691697672255 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |
| HERO 100 | 21990232555521 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 15 |
| HERO 84 HE JIS | 18691697672245 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 14 |

The extra blocker on the four rows at 15 is a firmware caveat their own
catalogue entry carries: two rows share a display name with a different model
id, and two share one with a different product id. Not proven interchangeable,
so not treated as such.

The shape of this table is the shape the evidence should produce. The board
somebody owns has the fewest blockers; the fourteen nobody has touched each
carry two more — their own identity binding, and a capability set inherited from
the family rather than established for them.

### What the four validation states mean, kept apart

- `FAMILY_HARDWARE_VERIFIED` — the family's semantics were established on real
  hardware. A family fact; every row may carry it.
- `PRODUCT_BINDING_VERIFIED` — a board answered with this model id **here**. One
  row.
- `PRODUCT_LAYOUT_VERIFIED` — the layers and key positions are this product's
  own vendor data, not a neighbour's. Every row.
- `PRODUCT_CAPS_VERIFIED` — the capability set was established for this product.
  One row; the rest carry `inherited_unconfirmed` capabilities, which is
  precisely not a per-product claim.

Collapsing these into one flag is how a board nobody has touched inherits a
physical result. They are four separate fields in `pcaps::ProductValidation` for
that reason, and `exactly_one_product_carries_a_per_product_verification` is the
test that holds it.

## Rank A: what it takes, and why nothing has it

Every clause has to hold at once — an `AND`, not a score, because a scoring
function lets a product with nine strong areas average its way past the one hole
a user would actually hit:

automatic exact identity · verified protocol family · family hardware semantics ·
a known layout · **every** mandatory capability closed · every exposed operation
production-safe · known bounds · bounded firmware constraints · known
persistence and read-back semantics · classified dangerous operations · no
unresolved contradiction · no manual learning required.

`hiding_a_capability_cannot_produce_a_rank` is the test for the specific failure
this design exists to prevent: an unmentioned capability counts as `Absent`, not
as skipped, and only `NotPresent` **with evidence** excuses a board from one.

## Data products

- `crates/pproto/src/aula_bytech_catalog.rs` — the generated Rust catalogue.
- `data/devices/aula-bytech-catalog.json` — the same facts as data.
- Both are produced by `python -m aula_kb_v3.emit_peripheral_catalog <worktree>`
  from `aula_kb_v3/registry_data.py`, deterministically, and the projection
  carries no research corpus: no vendor executables, no decompiled sources, no
  raw opcode catalogue. A consumer holding it can build a product surface and
  cannot assemble a frame. `tests/aula_kb_v3/test_projection_is_deterministic.py`
  asserts both halves.

## What happens next

The eighteen commands at the doors need one physical session on the HERO 84 HE.
Only what actually passes gets promoted; anything that echoes, refuses or
disagrees stays where it is, with the finding recorded.

The actuation incident's three-step test runs in the same session and is cheap:
read W's record and note byte 5, write once, read again.
