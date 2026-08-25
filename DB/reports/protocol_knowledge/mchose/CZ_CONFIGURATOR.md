# MCHOSE CZ: rendering the configurator, walking it, and what its bytes say

TICKET-25 consolidation pass 2. Continues `CZ_FAMILY.md`, which established the
envelope; this is about making the vendor's own UI run against a fake device and
reading what it emits.

No real device was involved. `assert_no_real_hid()` runs on the live page before
the first interaction, and the oracle refuses to start against a bootloader
identity.

---

## 1. Why the configurator would not render, and the one byte that fixed it

Zero payloads passed the envelope and the handshake but the UI threw
`Cannot read properties of undefined (reading 'index')` and drew nothing.

The chain, all quoted from vendor source:

```js
// CZ_SHARED_DATA/main.js
getInfo()              -> _simpleGetCommand(3, 0, 56)
getBase()              -> _simpleGetCommand(4, 0, 56)
getFuncConfig(profile) -> _simpleGetCommand(5, 64 * profile, 64)
getKeyMatrix(t, true)  -> _simpleGetCommand(7, 512 * t, 3 * maxKeyCount)

getDefaultKeyInfos(layer):
  n = await channel.getKeyMatrix(layer, true)
  for (i = 0; i < maxKeyCount; i++) [type, code1, code2] = n.slice(3i, 3i+3)

// /cizhou/_next/.../221.js -- the God60 layout
{ index: e => { let {defaultKeyDict: i} = e; return i[252].index + 1 } }

// CZ_SHARED_DATA/main.js -- how that dict is keyed
b = reduce(layer0DefaultKeys, (acc, key, i) => (acc[key.code] ??= {index: i, defaultKey: key}, acc), {})
```

So the layout dereferences `defaultKeyDict[252]`, which exists only if some
layer-0 key derives to code 252. **Every other code the layout references
resolves through `b[e.code]?.index ?? -1` and merely goes unplaced.** One key,
one crash.

Two vendor-derived facts supply it:

* the shipped `default-keys-god-60.js` — 128 records of `{type, code1, code2,
  code, name, index, layer}`, re-serialised as `[type, code1, code2]`, giving
  `3 x 128 = 384 = 0x180`, which is **exactly the region size measured earlier
  from 56 captured frames across 8 regions at stride `0x200`**. Measurement and
  source agree independently.
* the vendor's own `getKeyCode`, which maps `type=240, code1=250` to code 252.

**What is ours and stated as ours:** *where* that key sits. The shipped file has
no code-252 entry, so the harness puts one in the first placeholder slot
(index 64, a `type:0` entry). `required_code_patches` in
`analysis/cz_device_image_god60.json` records the triple, the slot, what it
replaced, and that the slot is a harness choice.

With that one key present the configurator renders in full: sections
**Освещение / Триггера / Настройка клавиш / Расш. клавиши / Производительность /
Другие**, the keyboard, Save, Reset, light effects, sliders, colour palette.

Everything served from the image is `synthetic_from_vendor_schema`. The echo
audit classifies all 443 walked frames exactly that way and every command
`EVIDENCE_VOID` — the correct result. This run established **request** structure.
It says nothing about any device.

## 2. The UI action inventory

`mchose_ui_walk.py` walks the sections, clicks controls one at a time, and
attributes every frame to the action that produced it.

Commands the walk exercised, with the vendor's own method names where the SDK
gives one:

| command | vendor method | frames | offsets | direction | class |
|---|---|---|---|---|---|
| `0x03` | `getInfo` | 6 | 1 | UNKNOWN | UNKNOWN |
| `0x04` | `getBase` | 5 | 1 | UNKNOWN | UNKNOWN |
| `0x05` | `getFuncConfig` | 96 | 2 | UNKNOWN | UNKNOWN |
| `0x06` | — | 42 | 2 | **WRITE** | POTENTIALLY_DESTRUCTIVE |
| `0x07` | `getKeyMatrix(default)` | 56 | 56 | UNKNOWN | UNKNOWN |
| `0x08` | `getKeyMatrix(user)` | 28 | 28 | UNKNOWN | UNKNOWN |
| `0x0b` | — | 136 | 8 | **WRITE** | POTENTIALLY_DESTRUCTIVE |
| `0x0c` | — | 37 | 37 | UNKNOWN | UNKNOWN |
| `0x0d` | — | 4 | 4 | **WRITE** | POTENTIALLY_DESTRUCTIVE |
| `0xa0` | — | 19 | 19 | UNKNOWN | UNKNOWN |
| `0xa9` | — | 1 | 1 | UNKNOWN | UNKNOWN |
| `0xf1` | — | 10 | 8 | UNKNOWN | UNKNOWN |
| `0xf2` | — | 3 | 2 | **WRITE** | POTENTIALLY_DESTRUCTIVE |

`0x06`, `0x08`, `0x0b` and `0xa0` were invisible until the UI ran; boot traffic
alone showed nine commands, the walk showed thirteen.

**Not one CZ frame is `SAFE_READ`,** and that is not caution for its own sake:
byte 4 means "bytes requested" for a read and "bytes supplied" for a write, so a
write of zeros is byte-identical to a read at the same offset. The one sound
inference runs one way — the vendor's read builder never places bytes after the
8-byte header, so a **non-zero trailing region proves a write**. The converse
proves nothing and is refused.

`NO_DESTRUCTIVE_PATH_FOUND` is not issued, and the tool cannot issue it.

### Coverage is bounded and says so

The walk caps controls per section and runs against a wall-clock budget, both
recorded in the artifact. **"Controls found" is not "controls that exist."** An
earlier unbounded run walked for thirty minutes, printed nothing, and had to be
killed — producing no evidence at all, which is worse than a partial walk that
states its limits.

### One defect worth naming

The first modal handler matched any `button.maicong-btn.type-primary` on the
page. Once the real dialog closed it went on clicking whatever primary button
the panel rendered — on this page, **"По умолчанию" (restore defaults)**. Nothing
reached hardware, but clicking a restore-defaults control while believing you
are dismissing a dialog is exactly the unlabelled-action hazard the inventory
exists to prevent. A modal is now required to be a dialog *container*, and its
text is recorded before anything is clicked.

## 3. Controlled sweeps

`mchose_cz_sweep.py` moves one control through five declared values and diffs
the frames. Byte 3 is `sum(bytes 4..) & 0xFF`, so it is separated as
`transport_derived` **before** the diff rather than explained after it.

Three fields, all in command `0x06`:

| record offset | encoding | control range | points |
|---|---|---|---|
| 9 | `byte == value` | 0–100 | 4 |
| 20 | `byte == value` | 1–15 | 4–5 |
| 35 | `byte == value / 30` | 60–1800 step 60 | 4 |

The `/30` relation holds at every observed point (480→16, 900→30, 1380→46,
1800→60), not on average.

**Each field was confirmed twice, for free.** A CZ write sends the same 64-byte
record as two chunks, at offsets 0 and 8. The field therefore appears at two
different payload offsets in one sweep, and reducing both to a *record* offset
makes them agree. Had they disagreed, the reading would be wrong somewhere.

**What is deliberately not claimed:** which UI section owns these fields. The
Производительность and Другие sections presented identical control sets, so the
section label is not established even though the byte positions are. Naming one
would be a guess dressed as a measurement.

Sweeps on the Триггера sliders (ranges 1–331 and 2–340, i.e. hundredths of a
millimetre) emitted **zero frames** at every value. Recorded as no result: the
app evidently defers those writes, and an empty capture is not a finding about
the protocol.

## 4. Provenance, stated once and kept

| layer | class |
|---|---|
| envelope, command names, key-code mapping, region sizes | **vendor source** |
| every reply the device gave | **`synthetic_from_vendor_schema`** — never an observation |
| the frames the UI emitted | **`OBSERVED_FROM_VENDOR_UI` in a synthetic environment** |
| where the patched key sits | **harness choice**, recorded as one |
| anything about real hardware | **nothing** |

The values the UI *displayed* came from bytes this harness supplied. Only the
values we *set* are ours to reason from, and the three fields above rest on
those.
