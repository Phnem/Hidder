# AULA KB V3 — End-to-End A-Gate

**Last recomputed 2026-08-24, after the knowledge-closure batch's *second*
physical pass.** The rank below is not stored anywhere; it is computed by
`crates/pproto/src/aula_bytech_rank.rs` from the ACL's own command classes and
the generated product catalogue, and printed by
`cargo run -p pproto --example rank_matrix`. Nothing in this document can be
raised without changing what is known.

```
FINAL RANK_A = 0/15
```

## What is closed, and what closed it

All eleven mandatory capabilities are now closed for the reference unit, and as
of exchange 011 every one of them has had the second physical pass this
project's discipline requires — see "What 'closed' does and does not mean"
below for what that does and does not buy.

| Capability | State | How |
|---|---|---|
| Identity | **CLOSED** | `read_model_id` `safe_read`. Exact product resolution through the catalogue, not through VID/PID — 14 of the 15 rows share `0x372E:0x103E`. |
| Remap | **CLOSED** | `read_keymap` `safe_read`, `set_remap` `safe_write`. Two physical passes, 2026-08-23 (exchange 008). |
| Macro | **CLOSED** | `set_macro_table` `safe_write`, assignment through `set_remap`. Two physical passes, 2026-08-23 (exchange 009), including witnessed playback. |
| HE actuation | **CLOSED**, with a caveat | `read_key_travel` `safe_read`, `set_key_travel` `slow_flash`. Physically validated 2026-08-19 (exchanges 005/006). See the open incident below. |
| Profiles | **CLOSED** | `read_profile_index`/`set_profile_index` `safe_read`/`safe_write`. Two passes, 2026-08-24 (exchanges 010, 011) — full round trip both times, visible RGB change on switch. |
| Polling | **CLOSED** | `read_polling_rate`/`set_polling_rate` `safe_read`/`safe_write`. Two passes, both directions each time, across the reconnect the write forces — the only command in this family whose write is never acknowledged. |
| Device settings | **CLOSED** | `read_os_mode`/`set_os_mode`, `read_win_lock`/`set_win_lock`, `read_key_combo`/`set_key_combo` `safe_read`/`safe_write`. Two passes each. |
| RGB | **CLOSED** | `read_light_mode`/`set_light_mode` `safe_read`/`safe_write`. The first pass settled the entry's own former weakest-evidence caveat — the real board answered all seven bytes and a real write frame was captured for the first time; the second round-tripped one unit of red through the production gate. |
| HE rapid trigger | **CLOSED** | `read_rapid_trigger`/`set_rapid_trigger` `safe_read`/`safe_write`. Two full cycles on W. |
| HE dead zone | **CLOSED** | `read_deadzone`/`set_deadzone` `safe_read`/`safe_write`. Two full cycles on W. |
| Per-key HE | **CLOSED** | Closes exactly when all three per-key analog writes do; all three do now. |

Also promoted, off-rank: `read_profile_name_slot0/1/2` (`safe_read`, read-only
by design — see below), `read_key_switch_type` (`safe_read`, real per-key data
with no vendor table yet to name it against), `set_auto_calibrate`
(`safe_write`, a one-byte flag and explicitly not the calibration procedure).

### What "closed" does and does not mean here

`capability_status` reads "Closed" the moment a command exists as
`safe_read`/`safe_write` in the ACL for the *family*. That is a statement about
the ACL, and it is the same statement whether the code behind it has been run
on a board once, twice, or never — the rank evaluator cannot see physical
passes and should not pretend to.

What backs the table above is therefore the exchange record, not the word
"Closed". This project's discipline (exchange 008's `NoBackup` and
cadence-collision bugs were both found only *after* promotion, by the pass that
exercised the actually-refactored production code) requires **two** physical
passes: the bootstrap-door pass that justifies promoting the ACL class, then a
second pass through the production `SafetyGate` the promotion produces.

As of exchange 011
(`docs/hardware/aula-bytech-exchange-011-second-pass-production.md` in the
Peripheral tree) that second pass has run for all twenty-two commands promoted
on 2026-08-24 — 32 checks, 32 passes, board byte-identical at the end. The same
two defect classes appeared again during this promotion, were caught against
the emulator, and did not reproduce on hardware.

**Still not what it means:** this is one board. Every row of the table is
evidenced on the single HERO 84 HE somebody owns, and the fourteen other
catalogued products inherit their capability set from the family rather than
having it established. That is a separate blocker and it is why fourteen rows
carry `manual learning still required`.

## The bootstrap doors, after 2026-08-24

Sixteen reads and nine writes went through the doors in one batch on
2026-08-24; twenty-two left, promoted on the physical evidence (exchange 010).
Three reads remain:

| Command | Why it stayed |
|---|---|
| `read_firmware_version` | Reply declared 1 data byte, decoder expects 2. Request-shape defect. |
| `read_rt_precision` | Reply declared 0 data bytes; a non-zero byte sat past the declared boundary. Same defect. |
| `read_supported_switches` | Same shape as `read_rt_precision`. |

All three built their request by reproducing a captured *connect-path* frame
verbatim, without accounting for this family's own established rule (already
true of `read_model_id`) that the request's own declared length constrains the
reply's. Not promoted; nothing here claims otherwise. The write door is empty
— all nine of its targets promoted.

`psafety`'s own guardrails (`exactly_the_planned_batch_is_awaiting_a_first_exchange`,
`the_door_holds_exactly_the_commands_that_are_mid_promotion`,
`the_aula_family_has_exactly_the_thirty_commands_it_earned`) hold the doors'
and the production surface's contents exact, so a command appearing or
disappearing unreviewed fails the build.

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
one-byte flag, reversible in one write, now `safe_write`. The two share a word
in their names and nothing else, and both ACL entries say so.

## Contradictions: three settled, one open

Four disagreements between this project's own two research paths were found
while porting this surface. Three are now settled:

1. **The `0x84` request length byte is not uniform across registers.** Settled
   by capture; the runtime reproduces each register's own observed shape.
2. **Register 1 is seven bytes, not three.** Settled by the vendor's own
   register table plus a worked example; the runtime uses seven, now confirmed
   in both directions on real hardware.
3. **The profile-name "unnamed" sentinel.** Settled 2026-08-24 by a real read:
   the board answers with a `0x38`-length body of fifty-six `0xFF` bytes, not
   the `0xFF`-length-byte form one source document described.

One remains open and blocks every product's rank, deliberately — a rank that
came out clean while it stood would be the wrong rank:

4. **Byte 5 of an actuation record: pad byte, or per-key scope flag.** Three
   independent observations agree `01` means "this key has its own value" and
   `00` means "it follows the board's"; Peripheral writes `00` on every
   actuation write it has ever made. This is the leading explanation for the
   unresolved `W 51→75→51 → 202/149` incident. A three-step test
   (read/write/read, watching byte 5) ran on 2026-08-24 and was
   **inconclusive by construction**: all four of W/A/S/D were already at
   `trailing: 0` before the write, so the `01→00` transition the test is built
   to catch could not be observed. Neither confirmed nor refuted. The
   read/write surface has no key left that sits at `01` to test against.

Full write-ups with the frames on all sides:
`Vetro hud/.claude/worktrees/.../docs/hardware/aula-bytech-request-shape-contradictions.md`
and `.../docs/hardware/aula-bytech-actuation-readback-anomaly.md`.

## A real backend bug found, and not yet fixed

Reconnecting through the desktop app's own `listDevices`/`connectDevice` IPC —
the only reconnect path anywhere in this codebase — failed repeatedly and
identically after the polling write's forced disconnect, resolving only after
a full process restart. `Worker::scan()`'s "already here" branch
(`crates/pcore/src/service.rs`) is the leading suspect (it refreshes
`known.present` every tick but never `known.discovered`, and `connect()` reads
its openable path from the latter) but was not confirmed as the sole cause,
and was **not patched speculatively**. Full record in exchange 010. The
wire-level polling write/read/persistence is proven; a working reconnect UX
for any command whose write disconnects the device is not.

## The 15 products

Every row: family semantics established on hardware (once, on the reference
unit), and the vendor's own per-product layout data. One row has anything more.

| Product | Model id | Validation states | Blockers |
|---|---|---|---|
| HERO 84 HE | 18691697672197 | FAMILY_HARDWARE, PRODUCT_BINDING, PRODUCT_LAYOUT, PRODUCT_CAPS | **4** |
| HERO 68 HE | 18691697672195 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 68 AIR | 18691697672212 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| WIN68HE Ultra | 18691697672213 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 68 MINI | 18691697672207 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 68 HE wireless | 19791209299969 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 7 |
| HERO 68 HE wireless | 21990232555523 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 7 |
| HERO 99 | 18691697672210 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 87 HE | 18691697672214 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 68 HE NEO | 18691697672218 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 68 HE JIS | 18691697672232 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 100 | 18691697672216 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 7 |
| HERO 99 HE | 18691697672255 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 100 | 21990232555521 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 7 |
| HERO 84 HE JIS | 18691697672245 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |

HERO 84 HE's four remaining blockers, exactly: the unread switch-type-table
bound (`read_supported_switches` still blocked), two unread precision scalars
(`read_travel_precision`, `read_rt_precision`, both still blocked or echoing),
and the one open contradiction. Every other row carries those same four plus
its own unverified identity binding and a capability set inherited from the
family rather than established for it (two more), and the four rows sharing
an ambiguous identity field with a sibling carry one further caveat (seven
total).

The shape of this table is the shape the evidence should produce. The board
somebody owns has by far the fewest blockers, all four of them things no
amount of *promotion* can satisfy — only more physical reads and the
contradiction's resolution can.

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
`a_capability_no_product_can_silently_stop_declaring` is its 2026-08-24
successor, checking the same property now that RGB itself is closed rather
than merely declared.

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

~~1. **The second physical pass.**~~ **Done, exchange 011.** All twenty-two
   promoted commands re-exercised through the refactored production
   `SafetyGate`: 13 reads, 8 write round-trips with rollback and a second
   read-back, `set_polling_rate` both directions. 32/32, board byte-identical
   at the end. Runner: `cargo run -p pcore --example second_physical_pass`.

1. **The request-shape fix** for `read_firmware_version`/`read_rt_precision`/
   `read_supported_switches` — widen the request the way `read_model_id`'s
   already does, then a short follow-up read. This is now the *only* thing
   standing between the reference unit and two of its four blockers: the
   switch-table bound and the rapid-trigger unit scalar both resolve on it.
2. **The reconnect-path bug** in `Worker::scan()` — exchange 011 narrowed it
   without fixing it. Three fresh-process reconnects across the polling write
   succeeded first time, so this is long-lived-process state rather than a
   device or backend fault. The `Peripheral::connect()` re-lookup finding a
   match for a path the staleness theory says should be gone is still the
   loose thread.
3. **The journal renders a pending write as a refusal.** `SafetyGate::write`
   reuses `Refusal::NeedsConfirmation` as its before-the-wire placeholder, so a
   hardware transcript reads `refused: NeedsConfirmation` for a write that was
   authorised and sent. No emulator test reads the journal's rendering, which
   is how it reached a transcript. Cosmetic, actively misleading, unfixed.
4. **The actuation contradiction** needs a key this project can read/write
   that is not already at `trailing: 0` — none exists on the current W/A/S/D
   surface. Exchange 011 added corroboration from a sibling record (W's
   rapid-trigger scope byte is a live `01` and survives a round trip intact),
   which makes the byte meaningful somewhere without testing the command in
   question. Widening the production actuation surface is still what would
   move it.
