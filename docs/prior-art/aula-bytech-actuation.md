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

- **Range and maximum travel.** The vendor's slider bounds come from a
  per-switch-type table (`maxTravel`), reached through `getSupportedSwitches`
  and a static list that lives in a chunk this pass did not resolve. The
  generic component's default of 0.1–3.4 mm is a *component default* for a
  different SDK module and must not be adopted for HERO 84 HE. Needed for a
  complete capability descriptor; not needed to read a value.
- **What our board actually answers** for travel precision. Everything above is
  the shape; the scalar is a number only the device has.

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
