# Exchange 004 — the key-id space, and the read that corrected exchange 003

Two `read_key_travel` bootstrap probes, one confirmation and one send each. The
first was aimed at the wrong keys; the second is the correction. Nothing was
written to the device at any point.

**Status: not promoted.** The decisive check still has not been performed — see
"What is still open" at the end.

## Provenance

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Board | AULA HERO 84 HE, `372E:103E`, the same unit as exchanges 001–003 |
| Endpoint | `0xFF60:0x0061`, report id 9 derived from the descriptor |
| Tool | `pproto/examples/aula_he_bootstrap.rs --confirm-probe-key-travel` |
| Vendor artifact | main bundle SHA-256 `9c6c6a71…` — **re-fetched and re-hashed, unchanged** |
| Vendor chunks | `trigger-DL4wDHU4.js`, `info-CRdif018.js`, `Bytech-D4yyggzn.js` |

## Why there was a second probe at all

Exchange 003 read four keys and got raw 79 from all of them. A validation
procedure was then agreed: set W, A, S and D to four *different* values in the
official configurator — 0.50, 1.00, 1.50, 2.00 mm — close it, and read back.
Expected raw 50, 100, 150, 200.

The read returned 79 on all four again, byte for byte the same answer as
exchange 003. Two explanations were on the table: the configurator write had not
happened, or it had landed in a slot we were not reading. The second was
investigated first, statically, before anything else was sent.

It was neither.

## The defect: the key id is not a HID usage

The vendor artifact settles it three separate ways.

**The layout tables are indexed by it.** Each board's layout is an array of
entries carrying a `keyValue`, and where the keymap lookup fails the vendor falls
back to `keyboardMap[Number(entry.keyValue)]` — indexing the family's key table
directly with that number. In that table the entry for W is at key `30` and
carries `browserValue: 26`.

**The two numbers appear as different fields of one record.** The keymap read
returns records the vendor matches as `record.id === entry.keyValue` and then
resolves as `keyboardMap.find(k => k.browserValue === record.keycode)`. `id` and
`keycode` are two fields of the same record, compared against two different
things. They are not the same number and the artifact never treats them as one.

**The travel command carries only the first of them.** `getKeyTravel` and
`setKeyTravel` are given `ids` taken straight from `layout.map(l => l.keyValue)`.
A HID usage never enters that command in either direction.

So, for the `bytech` family:

| Key | Protocol key id (`keyValue`) | HID usage (`keycode`) | What that usage names as a key id |
|---|---|---|---|
| W | **30** | 26 (`0x1A`) | `=` (Equal) |
| A | **43** | 4 (`0x04`) | F3 |
| S | **44** | 22 (`0x16`) | `8` (Digit8) |
| D | **45** | 7 (`0x07`) | F6 |

Our board's layout — the 84-key table, selected by model id `18691697672197` —
contains all eight of those ids. **Exchange 003 read four real keys that nobody
had ever configured**, and every check the decoder performs passed while it did
so: the frame was valid, the records were well formed, the checksum matched, and
each record answered for the id it was asked about, in order. The one thing that
could have caught it was a check on *which* ids were asked for, and that number
came from us.

## Probe — `read_key_travel` (`0x93:0x00`), corrected ids

```text
->  09 | 93 00 00 01 00 08 | 001E 002B 002C 002D | 00×48  B8
<-  09 | 93 00 00 01 00 14 | 001E 0028 01  002B 0028 01
                             002C 0028 01  002D 0028 01 | 00×43  08
```

after 1.0 ms.

| Key | Protocol key id | Raw travel | × 0.01 mm |
|---|---|---|---|
| W | 30 | 40 | 0.40 mm |
| A | 43 | 40 | 0.40 mm |
| S | 44 | 40 | 0.40 mm |
| D | 45 | 40 | 0.40 mm |

## What this establishes

**The key-id mapping, from the device rather than only from the artifact.** The
two id sets produce different answers on the same board in the same session:
travel 79 against 40, and a trailing record byte of `00` against `01`. A device
echoing our ids back would have produced identical payloads; two independent
fields differ, so the device is distinguishing the keys and the artifact's
mapping is the one it uses.

**That `0x93:0x00` is the slot the official configurator writes to.** The four
keys carry a different value from the rest of the board. WASD is the set the
vendor's own trigger page has a dedicated button for, so a previous session with
the official configurator is the ordinary explanation, and it means a
configurator write and our read address the same layer and OS mode.

**The layer/OS question, answered from the artifact and now corroborated.** The
trigger page hardcodes `layer: 0` on both the read and the write, and derives
`system` from `getOsMode()` read off the device — `0` for Win, `1` for Mac. Layer
is therefore never the variable; only OS mode is, and `0x00` reaching a slot the
configurator has written confirms this board is in Win mode.

**A round number, at last.** Exchange 003 recorded that 79 sits awkwardly with
the 0.01 scale, because a value the vendor UI wrote would be `mm ÷ 0.01` and so
0.80 mm would store as 80. 40 is exactly that: `0.40 ÷ 0.01`. It is the shape a
UI-written value was predicted to have, on keys a UI plausibly wrote, and it is
corroboration for the scale that exchange 003 could not obtain.

## What is NOT established

- **Per-key distinctness.** All four keys have carried one value in every read so
  far, 79 then 40. Nothing yet rules out a decoder that reports the first
  record's travel four times, or a request whose ids beyond the first are
  ignored. The ordering check makes both unlikely; only four different values
  make them impossible.
- **The scale, still.** 40 → 0.40 mm is consistent with 0.01 and is not proof of
  it. Our computation reproduces the vendor's, fallback included, so the two
  agree by construction and would be wrong together.
- **That `0x82:0x08` is supported.** Unchanged from exchange 003, and not
  re-probed here. It stays `bootstrap_probe` and stays unpromoted.

## Incidental observation, recorded and not explained

The fifth byte of each record — the one the vendor's decoder ignores and this
project's notes call a pad — is **not always zero**. It read `00` for the four
untouched keys and `01` for W, A, S and D. Both reads were of the same command
on the same board minutes apart, so this is a per-key field rather than noise. A
plausible reading is a "configured" or "enabled" flag, which fits which keys
carry which value, but that is a guess and is written down as one. It is not
decoded, not exposed, and not relied upon.

## What is still open

**The four-distinct-values check, now aimed at the right keys.** Set W = 0.50,
A = 1.00, S = 1.50, D = 2.00 mm in the official AULA configurator, close it
completely, and read back once. Expected raw 50, 100, 150, 200.

That single exchange settles the scale and per-key mapping together, and it is
the last thing standing between `read_key_travel` and promotion to `safe_read`.
