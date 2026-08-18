# Exchange 003 — the two actuation reads

Bootstrap probes for `read_travel_precision` and `read_key_travel`. One
confirmation each, one send each, no retry, nothing else sent.

**Status: not promoted.** One check remains and it is not one this machine can
perform — see "The question for a person" at the end.

## Provenance

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Board | AULA HERO 84 HE, `372E:103E`, the same unit as exchanges 001 and 002 |
| Endpoint | `0xFF60:0x0061`, report id 9 derived from the descriptor |
| Tool | `pproto/examples/aula_he_bootstrap.rs`, one flag per command |

## Probe 1 — `read_travel_precision` (`0x82:0x08`)

```text
->  09 | 82 08 00 01 00 00  00×56  6B
<-  09 | 82 08 00 01 00 00  00×56  6B      after 1.0 ms
```

**The answer is byte-identical to the request.** Length byte zero, no data.

This was sent twice. The first send was rejected by our own decoder, which
treated a zero precision as an error on the reasoning that a zero scale silently
turns every actuation point into 0.00 mm. That reasoning was right about the
consequence and wrong about the protocol: the vendor reads this byte with the
frame's length ignored and then computes `precision / 1000 || 0.01`, so zero is
a case it handles, not a fault it reports. The decoder was corrected against the
artifact and the probe re-sent. Two sends, both recorded, neither retried
automatically.

**What this establishes, and what it does not.** An answer identical to the
request is what an unsupported command looks like on boards in this class. With
an empty payload it is *indistinguishable* from a genuine "no precision
reported". So this exchange does **not** establish that the device supports
`0x82:0x08`.

It does establish something narrower and still useful: whatever the device is
doing, the vendor's own software reads the same byte from the same answer and
falls back to **0.01 mm per step**. Our reader now reproduces that exactly,
including the fallback, and reports which of the two it used.

## Probe 2 — `read_key_travel` (`0x93:0x00`, layer Normal, system Windows)

```text
->  09 | 93 00 00 01 00 08 | 001A 0004 0016 0007 | 00×48  1F
<-  09 | 93 00 00 01 00 14 | 001A 004F 00  0004 004F 00
                             0016 004F 00  0007 004F 00 | 00×43  D7
```

after 1.0 ms. This one is unambiguously a real answer: the length byte is `0x14`
— twenty bytes, four records of five — against the eight we sent, and it carries
data we did not.

| Key | HID usage | Raw travel | × 0.01 mm |
|---|---|---|---|
| W | `0x1A` | 79 | 0.79 mm |
| A | `0x04` | 79 | 0.79 mm |
| S | `0x16` | 79 | 0.79 mm |
| D | `0x07` | 79 | 0.79 mm |

Every record answered for the key it was asked about, in the order asked. The
decoder checks that rather than zipping the answers against our own list, so a
reordered reply would have been refused instead of silently attributing one
key's setting to another. Response checksum matched our computation on both
probes, which is now eight observations of that agreement.

## What is established

- **`0x93:0x00` is real.** The device answers it with per-key data, in the
  documented five-byte record layout, big-endian, in request order.
- **The record layout is confirmed**: key id `u16`, travel `u16`, one pad byte.
- **Four keys share one value**, consistent with a uniform setting rather than
  per-key tuning.
- The `aula-bytech` framing, checksum and response matching hold for a second
  command group, which is the first evidence that they are family properties
  rather than facts about `0x82`.

## What is not established

- **That `0x82:0x08` is supported at all.** See probe 1.
- **That 0.79 mm is the physical actuation depth.** What is known is that our
  computation and the vendor's produce the same number from the same bytes,
  because ours reproduces theirs including the fallback. If the fallback is
  wrong about this board, the vendor's configurator is wrong in exactly the same
  way and by exactly the same amount — which is why the comparison below is a
  check on our decoding, not on the device.
- **The range and step this board actually enforces.** Still unresolved, as
  recorded in the actuation note.

## Corroboration for the 0.01 scale, short of the real check

Not proof, but worth writing down because it narrows the field:

| Scale | 79 becomes | Verdict |
|---|---|---|
| 0.001 mm | 0.079 mm | below the vendor UI's own minimum of 0.1 mm — a value the UI could not have produced |
| **0.01 mm** | **0.79 mm** | inside the 0.1–3.4 mm the vendor's slider allows |
| 0.1 mm | 7.9 mm | more than twice the travel of any keyboard switch |

So 0.01 is the only one of the three that puts the observed value inside a range
the vendor's own software considers legal. That is consistent with the fallback
rather than independent of it.

One thing does not sit comfortably: **79 is not a round number.** A value the
vendor's UI wrote would be `mm ÷ 0.01`, so 0.80 mm would store as 80, not 79. A
stored 79 means either a factory default that is genuinely 0.79 mm, or a scale
slightly different from the fallback, or an offset in the encoding. This is
precisely the doubt the comparison below resolves, and precisely why nothing has
been promoted on it.

## The question for a person

**Open the official AULA configurator with this board connected, go to the
trigger page, and read the actuation value it shows for W, A, S and D.**

- If it shows **0.79 mm** — the decode is exact, and both commands can be
  promoted to `safe_read` on hardware evidence.
- If it shows **0.80 mm** — there is an off-by-one or a small scale difference,
  and the encoding needs another look before anything is promoted.
- If it shows anything else — the scale is wrong, and the raw 79 is the only
  thing this exchange established.

This is the check that turns "we applied a conversion" into "the conversion is
right", and it is the one step of the pipeline that a person has to do.
