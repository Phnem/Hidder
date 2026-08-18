# Exchange 001 — `aula-bytech` `read_model_id` on HERO 84 HE

The first command this project has ever sent to a device. One request, one
answer, nothing else.

This is an **evidence record**, not an ACL change. Nothing in the program
promoted anything as a result of this exchange; `data/protocols/aula-bytech.toml`
still says `bootstrap_probe` on `vendor_artifact`. What may now be promoted, and
what may not, is listed at the end for a person to decide.

## Provenance

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Board | AULA HERO 84 HE, `372E:103E`, release `0216`, strings `BY Tech` / `HERO 84 HE` |
| Endpoint | `0xFF60:0x0061`, interface 2 |
| Report id | **9**, derived from the board's own report descriptor, not configured |
| Tool | `cargo run -p pproto --example aula_probe -- --send` |
| Command | `aula-bytech::read_model_id`, class `bootstrap_probe`, key `0x82:0x01` |
| Authorisation | one `UserConfirmation`, one `ProbeGate`, consumed |
| Retry | none |
| Predicted before sending | `docs/prior-art/aula-bytech-readdeviceuuid.md` §9 |

## What crossed the wire

Request, 64 bytes (report id 9 + 63-byte frame):

```text
09 | 82 01 00 01 00 06 00 00 00 00 00 00 00 00 00 00
     00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
     00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
     00 00 00 00 00 00 00 00 00 00 00 00 00 00 6C
```

Answer, 64 bytes, **1.0 ms** after the write, first and only report in the
window:

```text
09 | 82 01 00 01 00 06 11 00 00 00 00 05 00 00 00 00
     00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
     00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
     00 00 00 00 00 00 00 00 00 00 00 00 00 00 56
```

Total elapsed, open to decoded: 2.8 ms.

The six data bytes are the whole of the device's answer and are reproduced in
full: they are a model identifier, shared by every HERO 84 HE, and carry no
per-unit entropy. The board's serial number was read during enumeration for the
"exactly one board is connected" check and is not recorded here, as in every
other capture in this directory.

## Decoded

```text
data      11 00 00 00 00 05
value     18691697672197        0x110000000005
series    0x11
index     0x05
```

## Every prediction, and whether it held

| Predicted | Observed | Verdict |
|---|---|---|
| Frame is 63 bytes + report id | 64 bytes each way | held |
| Report id derives to 9 | 9 | held |
| Request checksum `0x6C` at index 62 | accepted; the device answered | held |
| Response echoes group `0x82` | `0x82` | held |
| Response echoes subcommand `0x01` | `0x01` | held |
| Response echoes packet count 1, index 0 | 1, 0 | held |
| Data length byte is 6 | 6 | held |
| Six data bytes, big-endian | six | held |
| Shape `11 00 00 00 00 NN` | `11 00 00 00 00 05` | held |
| Series `0x11` for a wired board | `0x11` | held |

One thing was **not** predicted and is new:

**The device computes the response checksum the same way the request does.**
`0x56` is exactly what our own builder produces for that frame at report id 9
(`0xFF - ((9 + 0x82+0x01+0x01+0x06+0x11+0x05) & 0xFF)` = `0xFF - 0xA9` = `0x56`).
The vendor's driver never checks an incoming checksum, so this was recorded as
informational and deliberately not enforced. It now has an observation behind
it — one, on one board, for one command.

## What the value means

`18691697672197` is one of the ten ids the vendor's own HERO 84 HE module
carries, and it is one of the three the vendor's `initDeviceLayout` switches on
by name to select a distinct key layout. So the board answered with a value the
vendor's software recognises and treats as its own layout — which is a stronger
result than a well-formed frame alone: the answer is meaningful in the vendor's
own terms, not merely structurally valid.

It is a **model** identifier. Every HERO 84 HE will answer with this same value,
which is what makes it useful for identification and uninteresting for privacy.

## What this exchange establishes

`ProtocolEvidence::VerifiedExchange` for the intersection of three things, and
nothing wider:

```text
HERO 84 HE  ×  aula-bytech  ×  read_model_id (0x82:0x01)
```

Specifically now verified on hardware here:

- the `aula-bytech` frame layout, for a single-packet request;
- the checksum algorithm, in both directions, at report id 9;
- the response discriminator, for this command;
- the decode of a six-byte big-endian model id;
- that this board speaks `aula-bytech` at all;
- that this command is answered rather than ignored, and answered in ~1 ms.

## What this exchange does **not** establish

- **Not** that any other `bytech` board behaves this way. One board answered.
  The family stays `VendorArtifact` for the other nine models.
- **Not** that any other command in the family is safe, exists, or is encoded as
  believed. `0x82:0x02` is still a guess with a name.
- **Not** that group `0x82` is read-only. The absence of a `0x02` write group in
  the vendor bundle is a fact about the bundle. This exchange says one
  subcommand of `0x82` answers a question without visible side effects; it says
  nothing about the other fifteen.
- **Not** a measured cadence. One operation produces one latency, not a minimum
  gap and not a settle period. A `safe_read` needs both, and they need more than
  one exchange to measure.
- **Not** that the command is side-effect free. Nothing observable changed and
  nothing was expected to, but "we saw no effect" after one send is a weaker
  statement than "there is no effect", and it should be written the weaker way.

## What may now be promoted, for review

Proposed, not applied:

1. `data/protocols/aula-bytech.toml` — `read_model_id` from
   `class = "bootstrap_probe" / evidence = "vendor_artifact"` to
   `class = "safe_read" / evidence = "hardware"`, **once** a measured
   `[timing.class.safe_read]` exists for the family. It does not yet: that needs
   repeated exchanges, which is its own decision.
2. `pregistry` — a `ProtocolEvidence { family: "aula-bytech", confidence:
   Verified, source: VerifiedExchange }` for this board, so the family axis stops
   reading `unknown`.
3. `docs/prior-art/aula-bytech-readdeviceuuid.md` — the checksum section can drop
   "unverified in the response direction".

Left as candidates, unchanged:

- every other command in the family, including `getFirmwareVersion`
  (`0x82:0x02`), which is the obvious next one and is not being sent;
- `getBatteryStatus`, which the vendor calls on this board's connect path and
  whose result its own driver discards for this model;
- the whole `0x84`/`0x04` read/write group pair;
- every other `bytech` model.
