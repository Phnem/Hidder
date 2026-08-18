# `aula-bytech` actuation — the read chain, and the proof of its unit

Static findings from the AULA HUB bundle (SHA-256 `9c6c6a71…`, the same artifact
as before) plus the two lazy chunks HERO 84 HE loads for this page. **Nothing
here has been sent to any device.** No new command has been given a
`SafeCommandId`, a `ProbeCommandId`, or an ACL entry.

Read against `ember-actuation-model.md`, which supplied the questions. Ember
supplied no bytes and no ranges; the numbers below are AULA's own.

## The chain, end to end

```text
AULA "Trigger" page, per-key travel slider, shown in mm
  ← singleTravel = travel_raw × travelStep          (trigger module)
  ← travelStep   = getTravelPrecision() / 1000
  ← travel_raw   = u16 from getKeyTravel(...)       (performance service)
  ← command group 0x93, per-key records
```

Both halves are in the artifact, and the write path uses the same scalar in
reverse (`travel_raw = mm / travelStep`), which is the round-trip that makes the
reading unambiguous.

## The conversion, which matters more than the opcode

**The unit is micrometres, and the scale is read from the device rather than
assumed.**

```text
travel_mm = travel_raw × (travel_precision / 1000)
```

`travel_precision` is a single byte obtained from the device by its own command.
The vendor reads it once when the page mounts and derives *every* millimetre
figure on the page from it — actuation, both rapid-trigger sensitivities, and
the dead-zone values all multiply by the same scalar. It also drives the slider
step and the number of decimals shown.

So a device answering `40` does not mean 0.40 mm because 0.40 looks plausible.
It means `40 × precision / 1000` mm, and we must read the precision to know.
With a precision of 10 that is 0.40 mm; with 100 it would be 4.0 mm.

There is one loose end in the vendor's own code worth recording: when converting
*read* values it multiplies travel by `rtStep` rather than `travelStep`. Both are
assigned `travelPrecision / 1000` from the same call in the same statement, so
the two are numerically identical and nothing is wrong on the wire — but the
write path uses `travelStep`, so anyone reading the vendor code for a second
opinion should not be confused by the asymmetry.

## Commands

All read commands are `(group, subcommand)` pairs in the family's existing
scheme. **None of these has been sent from here.**

| What | Group | Sub | Request data | Response stride |
|---|---|---|---|---|
| travel precision | `0x82` | `0x08` | none | 1 byte, length forced by the caller |
| min rapid trigger | `0x82` | `0x06` | none | 1 byte, length forced |
| **per-key travel** | `0x93` | layer/system | `u16` key id × N | 5: id `u16`, travel `u16`, 1 pad |
| rapid triggers | `0x99` | layer/system | `u16` key id × N | 8: id `u16`, enable `u8`, press `u16`, release `u16`, 1 pad |
| dead zone ("safe area") | `0x96` | `0x00` | `u16` key id × N | 8: id `u16`, top `u16`, bottom `u16`, pad, enable |
| switch type per key | `0x95` | `0x00` | `u16` key id × N | 3: id `u16`, type `u8` |

All multi-byte fields are big-endian, consistent with the model-id read.

The two `0x82` reads sit in the group already exercised by `read_model_id`. The
others are new groups.

### KNOWN WRITE — NOT IMPLEMENTED

Recorded so they are recognisable and avoided. The high-bit convention holds
again: each is its read's group with `0x80` cleared.

| What | Group |
|---|---|
| set key travel | `0x13` |
| set rapid triggers | `0x19` |
| set dead zone | `0x16` |
| set switch type | `0x15` |

None of these will be given any command id, sent, or tested by this work.

## The key id is the vendor's own numbering, not a HID usage

Load-bearing, and established after the first hardware read rather than before
it — see `docs/hardware/aula-bytech-exchange-004-actuation.md` for what it cost.

Every per-key performance command in this family — travel, rapid trigger, dead
zone, switch type — identifies a key by the number the vendor calls `keyValue`.
That is a position in the family's own key table, not a HID usage code.

Three independent statements of it in the artifact:

- the family's key table is **indexed** by it: where the keymap lookup misses,
  the vendor resolves a key as `keyboardMap[Number(entry.keyValue)]`;
- the keymap read returns records with `id` **and** `keycode` as separate
  fields, matched against `keyValue` and `browserValue` respectively — two
  numbers, two comparisons, never interchanged;
- the travel commands are handed `layout.map(l => l.keyValue)` and no HID usage
  reaches them in either direction.

| Key | `keyValue` (protocol) | `browserValue` (HID usage) | What the usage names as a `keyValue` |
|---|---|---|---|
| W | 30 | 26 | `=` |
| A | 43 | 4 | F3 |
| S | 44 | 22 | `8` |
| D | 45 | 7 | F6 |

The mapping belongs to the **family**: one key table for `bytech`, and one layout
per model (68, 84 and 101 keys are the three present) selecting which of those
keys physically exist. So `keyValue` 30 is W on every board in the family, and
the layout answers whether a given board has it.

**Why this is the dangerous kind of mistake.** Both spaces are small `u16`s, both
are called "key id" in ordinary speech, and the overlap is total — every wrong id
is a valid id for a different key. A request built from the wrong space produces
a well-formed answer, in the right order, with a correct checksum, about keys
nobody asked about. `KeyId` is a newtype in `pproto` for exactly this reason.

## The subcommand byte carries layer and system

For the per-key commands the second byte is not a subcommand index but a packed
pair:

```text
byte1 = (layer & 0x03) | ((system & 0x07) << 2)
layer:  Normal 0, Fn1 1, Fn2 2, Tap 3
system: Windows 0, MacOS 1
```

For our board in its normal state that byte is `0x00`. Worth noting because it
means an actuation value is **per key per layer per OS mode**, not simply per
key — a distinction Ember does not have, and one that a capability model has to
carry or it will silently read the wrong layer.

**Which of the eight values the official configurator uses, exactly.** Its
trigger page passes `layer: 0` as a literal on *both* the read and the write, so
the layer is never a variable there — the other three layers are reachable by the
protocol and not by that page. The system half comes from the device: the connect
path calls `getOsMode()` and stores `Win` or `Mac`, and every per-key call then
sends `0` or `1` accordingly.

Two consequences worth stating. A configurator write and a read of `0x00` address
the same slot whenever the board is in Win mode, which is what makes a read-back
comparison meaningful at all. And on a board in Mac mode the same page would read
and write `0x04` throughout, so a fixed `0x00` in our code would silently address
a slot the user's own software never touches.

## Chunking: at most 11 keys per exchange

The vendor sizes each request chunk so that the *response* fits one frame:

```text
chunk_bytes = floor(usable_frame / response_stride) × request_stride
            = floor(56 / 5) × 2 = 22 request bytes = 11 key ids
```

11 records back is 55 bytes, one byte under the 56 a frame carries. So a
full-keyboard read of an 84-key board is 8 exchanges, not one.

**This is a real scope finding.** Our codec is deliberately single-frame: it
refuses a payload that would need chunking rather than silently writing twice.
Reading a handful of keys — W, A, S, D is four — is one frame each way and needs
nothing new. Reading the whole board needs multi-packet request *and* response
reassembly, which is its own piece of work.

## The four concepts, kept apart

Ember's checklist, answered for AULA:

| Concept | AULA source | Kind |
|---|---|---|
| Actuation point | `0x93`, `travel` field | setting, per key/layer/system |
| RT press (down) | `0x99`, `sensitivity.press` | setting, a delta |
| RT release (up) | `0x99`, `sensitivity.release` | setting, a delta |
| Dead zone top/bottom | `0x96`, `topHeight`/`bottomHeight` | setting, AULA-specific, absent from Ember |
| Live key travel | `0x98` monitoring | observation, arrives as **unsolicited events** |
| Calibration range | `0x94` events plus a range read | raw ADC, read only |

Two things follow that the model must respect:

- **Live travel is a bubble command.** `0x98` is one of the three ids the
  transport routes as unsolicited events, alongside `0x94` (calibration) and
  `0xFE`. Our codec already refuses to mistake those for answers, which was
  written before there was any reason to — it now has one.
- Actuation is an absolute depth; the two rapid-trigger values are deltas from a
  moving reference. Same unit, different meaning, exactly as in Ember. A decoder
  must surface them as separate fields even though they share a scalar.

## Not yet established

- **What our board actually answers** for travel precision. Everything above is
  the shape; the scalar is a number only the device has.

### Resolved 2026-08-18: where the travel bounds come from

Recorded here because the previous version of this note listed it as unresolved
and warned against adopting the wrong default. The warning stands and the source
is now identified.

The vendor's trigger page starts from component defaults —
`travelMin 0.1`, `travelMax 3.4`, `travelStep 0.01`, `defaultTravel 0.5` — and
then **replaces the maximum** with `maxTravel` for the switch the board actually
reports, via `getSupportedSwitches` filtered against a static table of 47 switch
types in the family chunk. Observed `maxTravel` across that table: 2.8 (×1),
3.0 (×4), 3.3 (×2), 3.4 (×29), 3.5 (×9), 3.7 (×1), 3.8 (×1) mm.

So the maximum is **a property of the switch, read from the device**, and 3.4 is
merely the most common value and the pre-read placeholder. A capability
descriptor that hardcodes 3.4 would be wrong on eighteen of the vendor's own
switch types. The minimum, 0.1 mm, and the step are not switch-dependent in the
artifact; the step is the travel precision, which is read.

Neither `getSupportedSwitches` nor `getKeySwitchType` has an ACL entry, a
`SafeCommandId` or a `ProbeCommandId`, and neither has been sent.

## Proposed next step

Two commands must be bootstrapped, not one, and they are independent:

1. `read_travel_precision` (`0x82:0x08`) — without it a travel value cannot be
   converted, so it comes first. Same group as the verified `read_model_id`,
   which is corroboration and not evidence.
2. `read_key_travel` (`0x93:layer|system`) — for a small, named set of keys
   (W, A, S, D), which is one frame each way.

Each follows the pipeline that already worked: ACL entry as `bootstrap_probe` on
`vendor_artifact`, one explicitly confirmed exchange, typed semantic validation,
`VerifiedExchange` recorded, human review, then promotion to `safe_read`.

Success is a value that matches what the official AULA configurator displays for
the same keys on the same board — which is the only check that proves the
conversion rather than merely applying it.
