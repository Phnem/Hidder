# Pre-registration — TICKET-26 calibration gate, AULA HERO 84 HE

Written **before** any of the runs below was scored, and committed before the
scorer was first invoked. Playbook v4 §1.3.

> blind here means no algorithm changed after the count was taken — not that no
> intermediate value was ever seen

That caveat is not decoration. The author of this document had already read the
ticket, which publishes v1's and v2's failing checks, including the three record
strides and the wrong `sub_offset`. What is being claimed is narrower and
checkable: **no engine source, no threshold and no dataset changed after any
score was produced.** The manifest hashes make that claim falsifiable rather
than merely asserted.

## What is being submitted

Three runs, all frozen before any of them is scored.

| label | engine | corpus | status |
|---|---|---|---|
| `v3_broken_control` | `engine_broken_control` | `benchmark/` | negative control — expected **RED** |
| `v3_engine_only` | `engine_v3` | `benchmark/` (unchanged, byte-identical to the datasets v1 was frozen on) | gate submission |
| `v3_engine_and_data` | `engine_v3` | `benchmark_gapclosed/` = the same datasets plus the 0x83 gap-closure frames | gate submission |

The engine change and the data change are separated on purpose, in two commits
and two runs, so that the effect of each is readable on its own. Both commits
land before the first score is taken; neither is a reaction to a number.

Gate modes are **A_RAW_ONLY** and **B_CONTROLLED_ACTIONS** only.
**C_PARTIAL_PROTOCOL is handed a redacted copy of the family schema and is
never a gate submission.** It is recorded and reported because it answers a
different question — what prior family knowledge buys — and because a mode C
that scores well while A and B score badly is a diagnosis, not a partial pass.

## Gate criteria — playbook §3.2, none of them relaxed

| metric | requirement |
|---|---|
| `HIGH_CONFIDENCE_WRONG` | **strictly 0** |
| `EXACT_PACKET_MATCH` | ≥ 0.9 |
| `BYTE_ACCURACY` | ≥ 0.999 |
| `FIELD_OFFSET_ACCURACY` | ≥ 0.95 |
| `ENDIANNESS_ACCURACY` | 1.00 |
| `CHECKSUM_RECOVERY` | yes |

`EXACT_PACKET_MATCH` and `BYTE_ACCURACY` are published together, always (§3.3).
A byte accuracy quoted alone hides the failure mode where one wrong structural
offset leaves every single frame unbuildable.

## What was changed, and the justification for each — none of it derived from truth.json

**C-1 — singleton guard in the sub-opcode search.** `discover_opcode_offset`
skipped groups of one; `discover_sub_offset` did not. A partition that isolates
every frame makes all remaining bytes vacuously constant and therefore scores
near-perfectly. In v3 both searches call the same `_split_score`, in which a
group of one contributes nothing to the numerator and still counts in the
denominator.

**C-2 — votes weighted by evidence volume.** One vote per opcode treats a
command seen four times and a command seen twelve hundred times as equal
evidence. v3 weights each opcode's vote by the frames behind it and reports the
evidence count in frames.

**C-3 — a prior moves ranking, never confidence.** v2 raised confidence when a
command's leading values fell inside an id space established elsewhere in the
family. In v3 a prior may reorder candidates, and only among candidates the
observed data already admits; confidence is computed from evidence alone. A
prior that contradicts the observed lengths is refused outright.

**O-3 — stride by rule, not by statistic.** A record size must divide every
observed body length, so the candidates are exactly the divisors of their gcd,
and the gcd is the most parsimonious of them. The consequential half is the
confidence:

* gcd prime → one candidate → the stride is pinned by the evidence;
* gcd composite → every proper divisor fits the same lengths → **under-determined,
  and the engine must not sound certain**;
* shortest observed body ≠ gcd → no frame ever carried a single record → the
  reading is a bound, not a sighting → confidence capped.

None of those three clauses can be evaluated against an answer key; they are
properties of the evidence. Lowering confidence where the evidence does not
determine the answer is the metric working as designed, not a threshold being
weakened — `HIGH_CONFIDENCE_WRONG` asks whether we lie confidently, and an
honest "I cannot tell 2 from 6 here" is not a lie.

**Data — the 0x83 collection gap.** Every 0x83 body in `benchmark/` is 6 or 18
bytes and 2, 3 and 6 all divide both, so no engine can settle that stride from
that corpus. `raw/physical_macro_assignment_20260823.jsonl` contains two 0x83
requests with a two-byte body and has done since 2026-08-23; they never reached
the corpus because the loader read `hex` and `hex_prefix` but not `hex_full`,
the spelling that session used. Provenance that travels with those frames: the
two 0x83 requests were composed by the capture session from the vendor bundle's
own serializer rather than emitted by the vendor UI; the device's replies are
genuine. A reader who considers a host-composed request inadmissible should
read `v3_engine_only` and ignore `v3_engine_and_data`.

## Stated in advance, so it can be wrong

1. `v3_broken_control` comes back RED, with `HIGH_CONFIDENCE_WRONG` ≥ 1.
2. `v3_engine_only` fixes `sub_offset` to 1, which should take
   `FIELD_OFFSET_ACCURACY` to 1.0 and `EXACT_PACKET_MATCH` to 18/18, because
   reconstruction depends on framing and on nothing else.
3. `v3_engine_only` predicts stride 6 for 0x83 — **still wrong** — but below the
   high-confidence threshold, so `HIGH_CONFIDENCE_WRONG` should be 0.
4. `v3_engine_and_data` predicts stride 2 for 0x83 at high confidence.
5. `ENDIANNESS_ACCURACY` is the change's main unforced risk: v3 names larger
   strides for 0x16 and 0x19, which changes which byte pairs the endianness test
   sees. If it drops below 1.00 the gate is RED and stays RED.

## Procedure

```
python -m miner.inference.freeze   --engine engine_broken_control --label v3_broken_control
python -m miner.inference.freeze   --engine engine_v3 --label v3_engine_only
python -m miner.inference.freeze   --engine engine_v3 --label v3_engine_and_data \
                                   --bench <corpus>/benchmark_gapclosed
# only now is ground_truth/truth.json opened, by score_v3 and by nothing else
python -m miner.inference.score_v3 --label v3_broken_control
python -m miner.inference.score_v3 --label v3_engine_only
python -m miner.inference.score_v3 --label v3_engine_and_data
```

`score_v3` re-checks the manifest against its own sha256 digest, every hashed
engine and scorer source, the dataset files by recorded path, and the frozen
predictions, and refuses to open the answer key on any drift. It has **no
`--allow-drift`**; `score.py` has one, and a gate with a documented bypass is
not a gate.

Each run is scored **once**. Whatever comes back is what is reported.
