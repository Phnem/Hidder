# Exchange 002 — timing characterization for `aula-bytech::read_model_id`

Five exchanges of one already-verified command, to establish a cadence the ACL
can carry. Not a benchmark, and deliberately not a search for the device's
limits.

## What question this answers

Exchange 001 established what `read_model_id` *means*. It said nothing about how
often it may be sent, and a `safe_read` cannot exist without a measured cadence.

The question asked here is narrow: **does this command work reliably at an
interval Peripheral is willing to be limited to?** Not "how fast can it go".
A model id is read once on connect, so a conservative ceiling costs nothing, and
looking for the floor would mean deliberately pushing a board towards the
failure mode this project exists to avoid.

## Provenance

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Board | AULA HERO 84 HE, `372E:103E`, the same physical unit as exchange 001 |
| Endpoint | `0xFF60:0x0061`, report id 9 derived from the descriptor |
| Command | `aula-bytech::read_model_id`, `0x82:0x01` |
| Script | fixed in the tool, not assembled from arguments |
| Exchanges | 5 |
| Interval | ≥ 1000 ms, enforced as a quiet period after each answer |
| Retry | none |
| Other commands | none, at any point |

Stop conditions, all of which would have ended the run where they happened: any
frame that failed typed validation, any transport error, any stall, any change
in the returned value. None fired.

## Result

| # | Round trip | Value | Response checksum |
|---|---|---|---|
| 1 | 2.8 ms | `0x110000000005` | matched |
| 2 | 2.8 ms | `0x110000000005` | matched |
| 3 | 2.6 ms | `0x110000000005` | matched |
| 4 | 2.5 ms | `0x110000000005` | matched |
| 5 | 2.8 ms | `0x110000000005` | matched |

5/5 answered, every answer structurally valid, every answer identical, every
response checksum equal to our own computation, no stall.

## What this licenses

Exactly one sentence, and the ACL entry says it in the same words:

> Peripheral allows `read_model_id` at most once per 1000 ms, and that regime
> was exercised on our own hardware.

It does **not** establish the device's minimum interval. Nobody measured one and
nobody looked. The 1000 ms is a ceiling this project chose before the run, not a
floor the device revealed.

The number is recorded as `[timing.command.read_model_id]`, not as
`[timing.class.safe_read]`. A measurement is evidence about the thing measured:
writing it on the class would have claimed the same cadence for every
`safe_read` this family ever gains, including commands nobody has sent. The
ACL's resolution order — command, then class, then nothing — exists for this.

## Response checksum: now six observations, still not enforced

Six exchanges have now seen the device compute the response checksum exactly as
our builder computes the request's, report-id seed included. That is a real
finding and it is recorded.

It is still not a validator. The vendor's own driver never checks an incoming
checksum, so enforcing it would be a rule this project invented on six samples
of one command on one board. `ModelIdRead` carries the verdict as
`checksum_ok`; nothing branches on it. If a later command in this family
produces more observations, turning it into a validator is a separate change
with its own evidence.

## What changed as a result

- `read_model_id` promoted by hand from `bootstrap_probe` / `vendor_artifact`
  to `safe_read` / `hardware`, with the command-level timing above. Nothing in
  the program performed that promotion.
- `ProbeCommandId` is now empty, which is the steady state: nothing is awaiting
  a first exchange.
- The bootstrap tooling (`aula_probe`, `aula_timing`) was removed once the
  command it existed to bootstrap no longer needed bootstrapping. Both are
  recoverable from git history at the promotion commit, and the procedure they
  implement is described in `psafety::probe`.

## The model id as an identification signal — recorded, not wired up

`0x110000000005` is stable across six reads on this board. It is tempting to add
it to the registry as a product signal, because nine models share
`372E:103E` and this would tell them apart.

It is **not** wired in, for a specific reason. The vendor artifact does not
contain the mapping: `18691697672197` appears only in a list of ids the module
recognises and in a layout switch, and the name "HERO 84 HE" is nowhere near
either. The four ids the vendor *does* name are all `0x12`-series wireless
models. So the association "this id means HERO 84 HE" rests on our own hardware
and on one physical unit.

That is enough to record and not enough to match on. What is written down:

- observed on our HERO 84 HE, six times, 2026-08-18: `0x110000000005`;
- the vendor's own software gives this id its own key layout, so it is
  model-distinguishing in the vendor's terms;
- the vendor artifact does not associate it with any product name.

Turning it into a matching signal needs either a second unit answering the same
value, or a vendor artifact that names the mapping. Until then, inventing the
link would be exactly the failure mode this registry was built to avoid: a
confident answer resting on one observation.
