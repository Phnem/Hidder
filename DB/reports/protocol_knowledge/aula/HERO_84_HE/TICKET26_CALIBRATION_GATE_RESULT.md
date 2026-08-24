# TICKET-26 — calibration gate result: **GREEN**

AULA HERO 84 HE, `372E:103E`, family `kb_by_v3_wired`. Playbook v4 §3.
Run and scored 2026-08-24. Pre-registration: `PRE_REGISTRATION_TICKET26.md`,
committed before the scorer was first invoked.

> blind here means no algorithm changed after the count was taken — not that no
> intermediate value was ever seen

## Verdict

**GREEN**, in both gate modes, on both submissions.
**`HIGH_CONFIDENCE_WRONG = 0`** — the criterion that cannot be relaxed.

The gate was red and had never been passed. It is now green, which under §3
means MCHOSE is unblocked for A Preview and for its first signed bundle. That
follows from the gate and nothing else: §9 is explicit that a green gate on
AULA does **not** demonstrate correctness on an unfamiliar family. It
demonstrates that the pipeline is not lying confidently about the one family
where we can check.

AULA's own HERO84 rank is untouched and stays frozen at A Preview. This ticket
repaired the inference engine, not our knowledge of the device.

## The six gate metrics — modes A and B, published together

`EXACT_PACKET_MATCH` and `BYTE_ACCURACY` appear together, always (§3.3). A byte
accuracy quoted alone hides the failure mode in which one wrong structural
offset leaves every single frame unbuildable, which is precisely what v1 did.

Modes A_RAW_ONLY and B_CONTROLLED_ACTIONS produced identical numbers in every
run; one column serves for both.

| | HCW | EXACT_PACKET_MATCH | BYTE_ACCURACY | FIELD_OFFSET | ENDIANNESS | CHECKSUM |
|---|---|---|---|---|---|---|
| **§3.2 requirement** | **strictly 0** | ≥ 0.9 | ≥ 0.999 | ≥ 0.95 | 1.00 | yes |
| `scores.json` v1 — pre-registered baseline | 1 | 0/18 | 0.9648 | 0.75 | 1.00 | yes |
| `scores_v2.json` v2 — self-disqualified, post-hoc | 3 | 0/18 | 0.9648 | 0.75 | 1.00 | yes |
| `v3_broken_control` — negative control | **4** | 0/18 | 0.9648 | 0.75 | 1.00 | yes |
| **`v3_engine_only`** — submission | **0** | **18/18** | **1.0000** | **1.00** | **1.00** | **yes** |
| **`v3_engine_and_data`** — submission | **0** | **18/18** | **1.0000** | **1.00** | **1.00** | **yes** |

Supporting counts for both green runs, modes A and B: `WRONG_BYTES 0/1134`,
`UNKNOWN_BYTE_RATE 0.0`, abstentions none.

### Mode C — recorded, **not a gate submission**

Mode C is handed a redacted copy of the family schema. It answers "what does
prior family knowledge buy", not "what does the pipeline recover from vendor
artifacts alone", and it can never be cited as a pass.

| run | HCW | EXACT | BYTE | FIELD_OFFSET | FIELD_TYPE | ENDIAN | CHK |
|---|---|---|---|---|---|---|---|
| v1 | 1 | 0/18 | 0.9648 | 0.75 | 0.25 | 1.00 | yes |
| v2 (post-hoc) | 3 | 18/18 | 1.0000 | 1.00 | 0.25 | 1.00 | yes |
| `v3_engine_only` | 0 | 18/18 | 1.0000 | 1.00 | 1.00 | 1.00 | yes |
| `v3_engine_and_data` | 0 | 18/18 | 1.0000 | 1.00 | 1.00 | 1.00 | yes |

v2's headline — mode C perfect while A and B score zero of eighteen — was the
diagnosis, not a near miss: blind, the engine was not recovering framing. Under
v3 modes A and B reach mode C's reconstruction score without being told
anything, which is what closing that gap looks like.

## The negative control, which is why the two GREENs mean anything

`engine_broken_control` is v1's already-defective framing search with the
confidence clamp removed — wrong, and sure. Frozen by the same freezer, scored
by the same scorer, one run before the submissions: **RED, HCW 4**, failing
`HIGH_CONFIDENCE_WRONG`, `EXACT_PACKET_MATCH`, `BYTE_ACCURACY` and
`FIELD_OFFSET_ACCURACY` in both gate modes. A gate that cannot fail proves
nothing when it passes.

The freeze check was also exercised against a real frozen run: editing one
stride inside `predictions_A_RAW_ONLY.json` after freezing makes `score_v3`
refuse to open the answer key at all (`FREEZE VIOLATION: predictions_...json
changed since the freeze`). There is no `--allow-drift`.

## What moved each number

**Engine (commit `b1a0c16`), corpus untouched.** The datasets under
`v3_engine_only` are byte-identical to those v1 was frozen on — verified by
comparing the two manifests — so this row is like-for-like with the v1 baseline
and the engine is the only variable.

* `sub_offset` 7 → **1**. C-1 and C-2 together. Offset 7 shattered five record
  commands into singletons; the singleton guard now costs it its score, and
  weighting votes by frames puts 1458 frames behind offset 1 against 929 behind
  offset 7. This one correction carries `FIELD_OFFSET_ACCURACY` 0.75 → 1.00 and
  `EXACT_PACKET_MATCH` 0/18 → 18/18, because packet reconstruction depends on
  framing and on nothing else. `BYTE_ACCURACY` 0.9648 → 1.0000 and
  `UNKNOWN_BYTE_RATE` 0.0317 → 0 follow from the same fix: with offset 1
  occupied, the check byte's range is fully known and the checksum computes.
* Strides 0x16 and 0x19: 2 → **8**, both now correct, at confidence 0.33 and
  0.30 — right answer, honestly hedged, because a body of 8 is equally
  consistent with strides 2, 4 and 8.
* `HIGH_CONFIDENCE_WRONG` 1 → **0**. The single v1 offender, `record0x83.stride`
  at confidence 0.935, is still wrong in this run (6, truth 2) but now carries
  confidence 0.45, because its gcd of 6 leaves 2, 3 and 6 all consistent and the
  engine says so instead of asserting.

**Data (commit `389ea7e`), engine untouched.** `benchmark_gapclosed/` is the
frozen datasets plus five transactions, ids 2519–2523, appended at the end so
every existing id and the entire held-out set stay identical.

* `record.0x83`: stride 6 at confidence 0.45 → stride **2 at confidence 0.95**,
  correct and now legitimately confident, because a body of 2 bytes was
  observed and 2 is prime. `FIELD_TYPE_ACCURACY` 0.75 → 1.00 in modes A and B.
* No gate metric moved. The engine had already taken all six to their
  requirement; the data change converts one honest abstention-level answer into
  a correct high-confidence one.

That split is the whole reason the two commits are separate: the gate was
passed by the **engine** repair alone. The collection gap was worth closing,
and closing it did not rescue anything.

## What is still wrong, and is not hidden

* **`get_set_rule`** predicts `add_0x80`; truth says `or_0x80`. Confidence 0.45,
  so it is not `HIGH_CONFIDENCE_WRONG`, and it is not one of the six gate
  criteria. On this opcode set the two rules are the *same function* — every SET
  opcode observed has bit 7 clear, so `s | 0x80`, `s + 0x80` and `s ^ 0x80` agree
  on all of them, and the engine picks `add_0x80` only because wrap-around makes
  it produce twice as many ordered pairs. §3.5 names exactly this normalisation
  (`bit7_of_opcode` ↔ `or_0x80`) as a harness concession, but the scorer was not
  touched for this ticket and still demands the literal string. 15/17 and 16/17
  checks pass in the two runs; this is the one that never does.
* **`record.0x83` in `v3_engine_only`** remains wrong at stride 6. That run's
  corpus genuinely cannot settle it. This is what an under-determined answer is
  supposed to look like on the scoreboard: wrong, and not confident.

## Reproducing

```bash
cd DB/protocol-miner
../.venv/Scripts/python.exe -m miner.inference.gap_closure
../.venv/Scripts/python.exe -m miner.inference.freeze --engine engine_broken_control --label v3_broken_control
../.venv/Scripts/python.exe -m miner.inference.freeze --engine engine_v3 --label v3_engine_only
../.venv/Scripts/python.exe -m miner.inference.freeze --engine engine_v3 --label v3_engine_and_data \
    --bench <abs>/reports/protocol_knowledge/aula/HERO_84_HE/benchmark_gapclosed
# only now does anything open ground_truth/truth.json
../.venv/Scripts/python.exe -m miner.inference.score_v3 --label v3_broken_control
../.venv/Scripts/python.exe -m miner.inference.score_v3 --label v3_engine_only
../.venv/Scripts/python.exe -m miner.inference.score_v3 --label v3_engine_and_data
../.venv/Scripts/python.exe -m pytest miner/inference/tests -q
```

Manifest digests, which `score_v3` re-checks before it will read the answer key:

| run | sha256 of MANIFEST.sha256.json |
|---|---|
| `v3_broken_control` | `49e2421d0a5b57d6a677ce61b5c584f6e1784c6cf5d88330ebd1dc86e21767ad` |
| `v3_engine_only` | `714c9e9238b65e0dfe42bc395cdd681b4a6debfe1267aca9e7fd9ca902033c59` |
| `v3_engine_and_data` | `d2bf7181fafb24436c4fd6064bfc3989c0e8bb492eb76d9cede8f371227e50e2` |

The manifests, predictions and scores are `.json` and this repository ignores
`*.json` tree-wide, so they live on disk untracked — as the v1 freeze already
did. The digests above are committed as `.txt` beside them.

## The LLM pass was not run

§3.5 is optional and conditional on the deterministic engine meeting the gate
first. It does, in every criterion, so an LLM pass could only add unverified
claims to a passing result. Not run, by choice.

## Provenance caveats a reader is owed

1. The two 0x83 single-item requests in the gap-closure capture were composed by
   that capture session from the vendor bundle's own serializer rather than
   emitted by the vendor UI. The device's replies are genuine. Anyone treating a
   host-composed request as inadmissible evidence should read `v3_engine_only`,
   which passes the gate without them.
2. `observations/transactions.jsonl` no longer reproduces the datasets the
   benchmark was frozen on. The upstream emulator trace
   `reports/oracle/aula_web/HERO_84_HE/raw_full_trace.jsonl` has grown from 2425
   frames to 4324 since 2026-08-22 and nothing pinned its hash, which is a
   §1.3-step-1 failure that predates this ticket. It does not affect these
   results: both runs read `benchmark/` and `benchmark_gapclosed/` directly, and
   both dataset trees are hashed in the manifests.
