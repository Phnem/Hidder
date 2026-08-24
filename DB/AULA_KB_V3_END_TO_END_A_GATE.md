# AULA KB V3 — End-to-End A-Gate

**Last recomputed 2026-08-24, after exchanges 011–016.** The rank below is not stored anywhere; it is computed by
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

**Both empty**, which is this project's resting state rather than an unused
mechanism. Sixteen reads and nine writes went through in one batch; twenty-two
left on exchange 010's evidence and the last three on exchange 012's.

Those three — `read_firmware_version`, `read_rt_precision`,
`read_supported_switches` — stayed behind because their replies were rejected,
and the diagnosis recorded at the time was wrong. It said the *requests* had
asked for too few bytes. All three requests are byte-identical to the vendor's
own serializer templates and always were.

The difference is on the reply side. This project bounds a response's data by
the response's own declared length byte; the vendor's client never reads that
byte, slicing from the data offset to the checksum and taking a fixed width off
the front. On this board `0x82:0x02` declares one byte and carries two,
`0x82:0x03` and `0x82:0x06` declare none and carry four and one.

`decode_response_body` sits beside `decode_response` rather than replacing it.
Thirty commands are on a production path on the strength of the declared length
agreeing with the payload, and widening all of them would turn a short answer —
this family's way of saying "not supported" — into a plausible value. Only a
command whose vendor-side reader is on record may use it.

Two corrections came with it, both from reading the vendor source rather than a
prose summary of it:

- **The firmware version is little-endian.** The decoder had the bytes the other
  way round and nothing disagreed. The board sent `16 02` = `0x0216` = 534, and
  `0216` is what the vendor's own software displayed for this board — in hex,
  not the `0534` decimal would give.
- **`read_rt_precision` is not a unit.** The vendor converts rapid-trigger
  deltas with `precision_distance`, the scalar `read_travel_precision` answers,
  not this one. The byte is carried unnamed.

**Promotion is not understanding.** Two of these three answer without closing
anything: the switch table's 26 bytes are carried and not parsed, and
`0x82:0x06`'s byte has no established meaning. The rank evaluator was changed in
the same commit so that promoting them could not close a bound by itself.

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

## Contradictions: all five closed

Four disagreements were found while porting this surface, and a fifth item — a
defect, not a disagreement — replaced the last of them for part of a day. **The
list is now empty**, and every entry left it by being closed rather than deleted.

1. **The `0x84` request length byte is not uniform across registers.** Settled by
   capture; the runtime reproduces each register's own observed shape.
2. **Register 1 is seven bytes, not three.** Settled by the vendor's register
   table plus a worked example, confirmed in both directions on hardware.
3. **The profile-name "unnamed" sentinel.** Settled by a real read: a
   `0x38`-length body of fifty-six `0xFF` bytes.
4. **Byte 5 of an actuation record: pad byte, or per-key scope flag.** Settled
   (exchange 014). The vendor's parsers name the field in all three record
   shapes — `distance_global`, `rt_global`, `safe_area_global` — and the board
   was shown to *store* it: W's dead-zone scope driven `0 → 1` and its
   rapid-trigger scope `1 → 0`, each read back changed with every other byte
   held constant, each restored exactly. **Not padding.**
5. **`set_key_travel` wrote that byte as a hardcoded `0`.** The defect the
   previous item exposed. **Fixed** (exchange 016), and confirmed on the command
   itself — which was never possible before, because a write cannot move a byte
   it hardcodes:

   ```text
     before  [(30, 163, 0), (43, 163, 0), (44, 163, 0), (45, 163, 0)]
     after   [(30, 168, 1), (43, 163, 0), (44, 163, 0), (45, 163, 0)]
   ```

   W's scope moved, the value round-tripped exactly, A/S/D did not move. The
   write now carries `IndividualKey` — configuring one key is what that means —
   and the rollback carries the scope the board reported.

### The incident is still not the contradiction

That this byte produced the `W 51→75→51 → 202/149` read-back is now a
*demonstrated mechanism* rather than a hypothesis. It is still not a caught one:
the anomaly has not reproduced across four clean transitions (exchanges 006,
010, 014, 016). The mechanism being real does not make the incident explained,
and this project does not record it as such.

## The backend bug: reproduced, localised, fixed

Reconnecting through the desktop app's own IPC failed repeatedly after the
polling write's forced disconnect and recovered only on a full process restart.
This report previously named `Worker::scan()`'s stale `known.discovered` as the
leading suspect. **That theory is refuted.**

Exchange 013 kept one `Peripheral` alive across the re-enumeration and tried
three candidates in order:

| candidate | result |
|---|---|
| the session held before the write | **dead** — `WriteFile: (0x0000048F)` |
| a reopen from the pre-write discovery | works, path byte-identical |
| a reopen from a discovery taken after | works |

So neither the discovery nor the enumeration cache was stale. The only dead
thing was the `DeviceSession`'s handle — and `Worker::connect` returns early
while a session exists, so the application answered "connected" and then wrote
through it.

It looked intermittent because the watcher enumerates every two seconds and a
re-enumeration takes about as long: when a scan lands in the gap the device is
removed and the service recovers by luck, and when it does not, the entry
survives holding a dead handle for the life of the process. That is exchange 010
failing for minutes and exchange 011, from a fresh process, always working —
one defect, one coin toss.

**Fixed.** `WriteTarget::reenumerates_the_device` is its own predicate, and the
session is released the moment such a write is dispatched. Nothing enumeration
can see changes across the re-enumeration, so the write is the only moment the
program knows. Verified through the running `DeviceService` on hardware: write,
card no longer claiming a session, read of the new rate — one process, no
restart.

`known.discovered`'s enumeration half is refreshed in `scan` as well. That is a
separate latent defect on the same path, proven not to be this one.

## The 15 products

Every row: family semantics established on hardware (once, on the reference
unit), and the vendor's own per-product layout data. One row has anything more.

| Product | Model id | Validation states | Blockers |
|---|---|---|---|
| HERO 84 HE | 18691697672197 | FAMILY_HARDWARE, PRODUCT_BINDING, PRODUCT_LAYOUT, PRODUCT_CAPS | **2** |
| HERO 68 HE | 18691697672195 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| HERO 68 AIR | 18691697672212 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| WIN68HE Ultra | 18691697672213 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| HERO 68 MINI | 18691697672207 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| HERO 68 HE wireless | 19791209299969 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 68 HE wireless | 21990232555523 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 99 | 18691697672210 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| HERO 87 HE | 18691697672214 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| HERO 68 HE NEO | 18691697672218 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| HERO 68 HE JIS | 18691697672232 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| HERO 100 | 18691697672216 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 99 HE | 18691697672255 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |
| HERO 100 | 21990232555521 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 6 |
| HERO 84 HE JIS | 18691697672245 | FAMILY_HARDWARE, PRODUCT_LAYOUT | 5 |

HERO 84 HE's two remaining blockers, verbatim from
`cargo run -p pproto --example rank_matrix`:

```text
  bounds unknown: actuation ceiling: this board answers the switch table,
                  and nothing here can read it
  bounds unknown: he.rapid_trigger units: no rapid-trigger value has been
                  checked against an outside source
```

It carried four until 2026-08-24. Two closed:

- **the contradiction**, by fixing `set_key_travel` and confirming it on the
  command itself;
- **actuation units**, on the four-point cross-check against the vendor's own
  configurator — 0.51/1.02/1.49/2.00 mm written there, raw 51/102/149/200 read
  here, exact on all four. Not on `read_travel_precision`, which exchange 015
  confirmed this firmware does not implement: asked again under an echo detector
  that cleared three other empty-payload commands the same day, it still returns
  this project's own request body zero to the checksum.

  Gated on `product_binding_verified`. The vendor replaces its default scale
  with the one the device reports, so this is a fact about *this* board and a
  product nobody has touched inherits none of it.

**Neither of the two left is a code problem.**

| blocker | what would close it |
|---|---|
| actuation ceiling | the vendor's table of 47 switch types. This board answers `0x82:0x03` and reports switch type 14 per key; the mapping from a type to the travel it permits lives in the vendor's family chunk and is in no local artifact. |
| rapid trigger units | one rapid-trigger value set in the vendor's own software and read back here — the same check that fixed the actuation scale, never performed for this setting. |

Rapid trigger is deliberately not closed on actuation's cross-check even though
the vendor converts both with the same scalar. That chain rests entirely on the
static extraction being right about which scalar rapid trigger uses, nobody has
checked a rapid-trigger number against anything outside this project, and a
wrong scale there is a slider out by a factor of ten.

Every other row carries those two plus its own unverified identity binding and a
capability set inherited from the family rather than established for it, and the
four rows sharing an ambiguous identity field with a sibling carry one further
caveat.

**Those inherited blockers are why 15/15 is not reachable from this desk.**
`FAMILY_NOT_VERIFIED` and `manual learning still required` are cleared by a board
answering, and there is one board. Fourteen rows are the vendor's catalogue
saying which module a product uses — second-hand, enough to open a device
read-only, and not enough to call a product finished. Making them rank A without
the hardware would mean removing the check that says so.

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

Two things, and neither is code. Everything this project could close from the
desk is closed.

1. **The vendor's switch table.** `read_supported_switches` is production-safe
   and this board answers `3a e1 41 09`; `read_key_switch_type` reports type 14
   for every key it can address. What is missing is the mapping from a switch
   type to the travel it permits — 47 entries in the vendor's family chunk, of
   which this project has only the *distribution* (2.8 ×1, 3.0 ×4, 3.3 ×2,
   3.4 ×29, 3.5 ×9, 3.7 ×1, 3.8 ×1 mm). Type 14 could be any of them, so
   `TravelBounds` keeps the smallest ceiling and `SWITCH_TABLE_IS_PARSEABLE`
   stays `false`. Guessing here would widen a bound that governs writes.

2. **One rapid-trigger value, cross-checked.** Set a rapid-trigger sensitivity
   to a known figure in the vendor's own software, then read it back here. That
   is the whole of it, and it is the same check that fixed the actuation scale
   on exchange 004. Until somebody does it, no rapid-trigger number in this
   project has ever been compared against anything outside it.

### And then?

Fourteen products would still not be rank A, and no amount of work here changes
that. They carry `protocol family is high, not verified` and `manual learning
still required` because no board of those models has ever answered — the
catalogue says which module a product uses, which is second-hand and enough to
open a device read-only. Clearing those two is a hardware problem, not a
software one.

So the honest ceiling on this surface is **1/15**, and it is two evidence items
away.

### Closed since the last revision

- **The second physical pass** (011). Twenty-two promoted commands re-run
  through the refactored production `SafetyGate`. 32/32.
- **The request-shape defect** (012). Three commands promoted; both bootstrap
  doors empty. The diagnosis this report previously carried was wrong: the
  requests were byte-identical to the vendor's own templates all along, and the
  reply's declared length is what nobody should have been reading.
- **The reconnect defect** (013). Reproduced in one long-lived process with three
  candidate causes separated; the stale-path theory this report named is
  **refuted**. Fixed by releasing the session when a re-enumerating write is
  dispatched, verified through the running `DeviceService`.
- **The false journal rendering.** `Outcome::Dispatched` replaces a borrowed
  refusal. Authorization semantics untouched, three regression tests.
- **The scope byte** (014), and **`set_key_travel`** (016). The contradictions
  list is empty.
- **`read_travel_precision`** (015). Confirmed unimplemented on this firmware,
  under a detector that can now tell an echo from an answer. The unit gap it was
  blocking closed on better evidence instead.
