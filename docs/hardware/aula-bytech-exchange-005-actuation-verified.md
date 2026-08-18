# Exchange 005 — four values written by the vendor, four values read by us

The check the previous four exchanges were building towards. One read-only
`read_key_travel`, no write of any kind from Peripheral, and an exact match.

**Status: verified. This is the exchange that promoted `read_key_travel` to
`safe_read`.**

## Provenance

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Board | AULA HERO 84 HE, `372E:103E`, the same unit as exchanges 001–004 |
| Endpoint | `0xFF60:0x0061`, report id 9 derived from the descriptor |
| Tool | `pproto/examples/aula_he_bootstrap.rs --confirm-probe-key-travel` |
| Written by | the official AULA configurator, closed before this read |
| Written from | Peripheral: **nothing**. This project has never written to this board |

## The procedure

This is a two-party test, and the point is that the two parties share no code.

1. The official AULA configurator set four **different** actuation points on
   W, A, S and D, and displayed them: 0.51, 1.02, 1.49, 2.00 mm.
2. The configurator was closed completely.
3. Peripheral made one read-only exchange and decoded it independently.

Four distinct values rather than one repeated value, because that is what
separates a decoder that reads four records from a decoder that reads the first
record and reports it four times. Every previous exchange returned a uniform
value across all four keys and could not tell those apart.

## What crossed the wire

```text
->  09 | 93 00 00 01 00 08 | 001E 002B 002C 002D | 00×48  B8
<-  09 | 93 00 00 01 00 14 | 001E 0033 01  002B 0066 01
                             002C 0095 01  002D 00C8 01 | 00×43  B2
```

after 1.0 ms. One report in the window, no unsolicited events, response checksum
matching our computation.

## The result

| Key | Key id | Raw | Ours, `raw × 0.01` | Configurator shows | Δ |
|---|---|---|---|---|---|
| W | 30 | 0x33 = 51 | **0.51 mm** | 0.51 mm | 0 |
| A | 43 | 0x66 = 102 | **1.02 mm** | 1.02 mm | 0 |
| S | 44 | 0x95 = 149 | **1.49 mm** | 1.49 mm | 0 |
| D | 45 | 0xC8 = 200 | **2.00 mm** | 2.00 mm | 0 |

Exact on all four. No rounding was needed and no tolerance was used.

## Cadence characterisation

A `safe_read` has to resolve to a measured cadence -- `psafety` refuses one that
nothing can throttle -- so the same fixed-script procedure that earned
`read_model_id` its number was run for this command
(`pproto/examples/aula_key_travel_timing.rs`).

Five exchanges, at least 1000 ms apart, stopping at the first answer that
differed from the first. Nothing else sent, nothing written.

```text
  1/5   2.6 ms  checksum ok  W=0.51 A=1.02 S=1.49 D=2.00
  2/5   2.8 ms  checksum ok  W=0.51 A=1.02 S=1.49 D=2.00
  3/5   2.5 ms  checksum ok  W=0.51 A=1.02 S=1.49 D=2.00
  4/5   2.4 ms  checksum ok  W=0.51 A=1.02 S=1.49 D=2.00
  5/5   2.4 ms  checksum ok  W=0.51 A=1.02 S=1.49 D=2.00
```

All five identical, every response checksum matching, no stall. Round-trip times
match `read_model_id`'s 2.5–2.8 ms, which is the first evidence that the figure
is a property of the transport rather than of one command.

This licenses exactly one sentence, and it is written into the ACL note as such:
Peripheral allows this command at most once per second, and that regime was
exercised here. It is **not** a claim about the device's minimum interval, which
nobody measured and nobody looked for. Actuation is read on connect and after a
change, so a conservative ceiling costs nothing.

As a by-product it is also a sixth and seventh through tenth confirmation of the
four values above, taken over five seconds: the reading is stable, not a
one-off.

## What this establishes

Five separate things, each of which had a live alternative before this exchange.

**The key-id mapping is right.** Four keys, four ids, four values that differ
from each other in the way the person at the keyboard chose. The alternative —
that our ids name some other four keys whose values coincidentally happen to be
these — is not a serious reading of a four-way match.

**The record ordering is right.** The values are strictly increasing in the
order asked, and every record answers for the id it was asked about. A decoder
zipping answers against its own list would have been indistinguishable from a
correct one in every earlier exchange, because every earlier exchange returned a
uniform value.

**The decoder reads four records, not one.** The distinct-values property
directly excludes reporting the first record four times, which exchange 004
explicitly recorded as still open.

**The scale is 0.01 mm per raw unit.** Not corroborated — determined. Four
points, exactly linear through the origin, agreeing to the last displayed digit
with a value a person typed into another program. Two points would have fixed a
line; four agreeing is a much stronger statement than the "0.79 is not a round
number" doubt that exchange 003 opened and could not close.

Worth being precise about what carries the claim. Our figure is computed with
the vendor's documented fallback, so the vendor's *display* and ours agree by
construction and that agreement alone would prove nothing. What proves it is
that the number came out of a **human decision** — someone chose 1.49 mm — and
survived a round trip through the vendor's write path, the firmware's storage,
our read, and our conversion, arriving unchanged. That path has no shared code
in it.

**Interoperability, in the direction that matters.** An official write is
readable by Peripheral, on the same layer and OS-mode slot, with no negotiation.
This is the first evidence in the project that another program's configuration
of this board is legible to us.

## What this does NOT establish

- **That `read_travel_precision` (`0x82:0x08`) is supported.** Untouched here
  and not re-sent. Its status is unchanged from exchange 003: the board returned
  our own frame, which is indistinguishable from an unsupported command. It is
  now recorded in the ACL as `unknown`, which is the class that has no command
  id at all, so nothing in the program can send it.
- **That 0.01 is a family constant.** It is this board's scale, established on
  this board. Another `bytech` board may report a precision of its own, and the
  code still asks rather than assuming: `TravelScale` remains a two-case type
  and this board resolves to `VendorFallback`.
- **Anything about layers other than Normal, or OS modes other than Windows.**
  `0x93:0x00` is one slot. The vendor's own trigger page only ever uses layer 0,
  but the other three layers exist in the protocol and no exchange has touched
  them.
- **Anything about writing.** Peripheral has still never written to this board,
  and `0x13` — the write twin — has no command id and is not implemented.

## Correction to the record

The earlier reads were **not** failures of the protocol, the decoder or the
transport, and the ticket notes say so explicitly:

- The 79/79/79/79 of exchange 003 was a correct read of four keys nobody had
  configured, caused by a request built from HID usages instead of key ids. Our
  defect, found and fixed in exchange 004.
- The 40/40/40/40 of exchange 004 was a **correct read of the correct keys**.
  The intended new settings had simply not been applied in the official
  configurator at that point. Nothing about that exchange was wrong; the board
  was reporting its actual state, and it agreed with what the configurator had
  last written.

Both are worth keeping in the record. The first is a defect this project shipped
and caught; the second is an exchange that looked like a failure and was not, and
mistaking it for one would have led to changing working code.
