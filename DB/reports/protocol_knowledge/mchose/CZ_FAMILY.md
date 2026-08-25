# MCHOSE CZ: the fifth code base, its envelope, and what its frames do not say

TICKET-25 consolidation. Everything below is either quoted from the vendor's own
SDK or measured against a captured frame; where a claim rests on only one of
those, it says which.

No real device was involved at any point. `assert_no_real_hid()` runs on the live
page before the first interaction, and the oracle now also refuses to start
against a bootloader identity.

---

## The code base

The CZ keyboard configurator is **not** in the M HUB bundle. It is a separate
webpack build at `https://www.mchose.com.cn/cizhou/CZ_SHARED_DATA/`, loaded into
a same-origin iframe. Acquired via its own `asset-manifest.json` — 53 artifacts,
1.9 MB, **no sourcemaps** — so the closure is the vendor's own list rather than a
crawl of whatever happened to load. Manifest:
`acquisition/cz_sdk_manifest.json`.

One gap, stated rather than glossed: the `/cizhou/` **Next.js app** that renders
the configurator UI is a *different* asset tree (`/cizhou/_next/static/chunks/`)
and is **not** acquired. The SDK — the code that builds frames — is.

## The envelope

From `CmdQueueItem` in `main.<hash>.js`, verbatim:

```js
h = isArray(s.data) ? [s.data.length, lo, hi, 0].concat(s.data.map(e => 255 & e))
                    : [s.data.size,   lo, hi, 0];
c.data = [c.flag, c.command, 0, 255 & sum(h)].concat(h);
hJ = e => [255 & e, e >> 8 & 255];
CmdQueueItem.NORMAL_OPTIONS = {flag: 85, expect: 170, offset: 0};
```

| byte | meaning |
|---|---|
| 0 | flag — `0x55` request, `0xAA` expected in the reply |
| 1 | command |
| 2 | 0, constant in the builder |
| 3 | `sum(bytes 4..) & 0xFF` |
| 4 | size — **bytes requested** for a read, **bytes supplied** for a write |
| 5–6 | offset, 16-bit little-endian |
| 7 | 0 |
| 8.. | payload for a write, packet padding for a read |

Sent as `sendReport(0, …)` in a 64-byte packet. The reader, also verbatim:

```js
n = new Uint8Array(t.data.buffer.slice(0, 8));
r = n[0] === expect && n[1] === command
 && n[4] <= data[4] && n[5] === data[5] && n[6] === data[6];
```

### What is proven, and by what

* **Layout** — vendor source **and** byte-for-byte reproduction of a captured
  frame. `55 03 00 38 38` + 59 zeros decodes to command 3, size 56, offset 0,
  checksum 56, and 56 is exactly `sum([56,0,0,0] + zeros)`. Pinned by
  `tests/test_mchose_cz_codec.py` against the captured bytes, so a
  transcription error fails a test rather than becoming a confident document.
* **Checksum is a sum, not an XOR** — vendor source only. The captured frame
  **cannot** discriminate: over an all-zero payload sum and XOR agree. The codec
  records which claim rests on which evidence, so the frame is never cited for
  something it cannot show.
* **Command meanings** — nothing. This is an envelope, not a dictionary.

## The direction ambiguity, and its one-way escape

Byte 4 carries *bytes requested* and *bytes supplied*. So:

> **A CZ read and a CZ write of an all-zero payload are byte-identical.**

Structurally the same trap as BY's wired `0x04`, arrived at independently in a
different family. Neither informs the other.

There is exactly one sound inference, and it runs one way. The vendor's read
builder (`_simpleGetCommand` → `data: {size: N}`) **never** places bytes after
the 8-byte header. So a non-zero trailing region proves a **write**. An all-zero
trailing region proves nothing. The classifier uses the sound direction and
refuses the converse.

## Getting past the handshake

With no reply the runtime loops forever: `communicateEnabled:false`,
`failedCount` climbing, retry every 3 s, row disabled. Answering with a reply
built from the vendor's own schema — `0xAA`, same command, same offset, size ≤
requested, zero payload — settles it: `isBusy:false`,
`communicateEnabled:true`, `failedCount:0`, and the app enters the configurator
route.

**Every one of those replies is `synthetic_from_vendor_schema`.** It proves the
harness can satisfy the client's matcher and nothing else. The echo audit
classifies all 11 handshake frames `SYNTHETIC_FROM_VENDOR_SCHEMA` and every
command `EVIDENCE_VOID`, which is the correct and expected result — the run
established **request** structure only.

## What the app then does on its own

After its informational modal is dismissed, 121 frames across 9 command bytes:

| command | frames | offsets | chunk | regions | stride | class |
|---|---|---|---|---|---|---|
| `0x03` | 5 | 1 | — | 1 | — | UNKNOWN |
| `0x04` | 4 | 1 | — | 1 | — | UNKNOWN |
| `0x05` | 2 | 2 | 56 | 1 | — | UNKNOWN |
| `0x07` | 56 | 56 | 56 | 8 | **0x200** | UNKNOWN |
| `0x0c` | 37 | 37 | 56 | 1 | — | UNKNOWN |
| `0x0d` | 4 | 4 | 12 | 2 | — | **POTENTIALLY_DESTRUCTIVE (write)** |
| `0xa9` | 1 | 1 | — | 1 | — | UNKNOWN |
| `0xf1` | 9 | 7 | 56 | 2 | — | UNKNOWN |
| `0xf2` | 3 | 2 | — | 1 | — | **POTENTIALLY_DESTRUCTIVE (write)** |

`0x04` here is a **CZ** command byte. Its numeric collision with BY's wired
`0x04` lead is a coincidence and carries no connection whatsoever.

The only stride reported is `0x07`'s `0x200`, and only because **eight**
consecutive regions were observed with equal gaps. Every other command's stride
is withheld with the reason recorded; a gap between two regions is a difference,
not a stride.

**Not one CZ frame is `SAFE_READ`.** Every candidate has an all-zero trailing
region, which is precisely the case the envelope cannot distinguish from a write.

## Why the UI inventory is empty, and what that costs

The configurator renders nothing against synthetic zeros — it throws
`Cannot read properties of undefined (reading 'index')` while parsing what the
harness fed it. So **0 controls were found and 0 exercised**, and the inventory
records that as coverage rather than as a finding. Nothing here says the device
has no destructive UI path; it says nobody has walked it.

Making the UI render needs semantically valid config payloads. The honest ways
to get them are the vendor's own shipped defaults (`default-keys-god-60.js`) or
the parsers in the un-acquired Next chunks. Inventing plausible-looking bytes to
make the UI happy is not one of them: those bytes would be indistinguishable, a
week later, from something a device said.
