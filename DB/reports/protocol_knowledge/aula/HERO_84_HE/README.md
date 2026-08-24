# HERO 84 HE protocol corpus

AULA HERO 84 HE only. `372E:103E`, family `kb_by_v3_wired`, controller class
`Lt`. **Do not add other AULA models to this tree** — the point of keeping it
single-model is that family-transfer claims can be tested rather than assumed.

## Layout

```
raw/            packets as captured, no interpretation
observations/   the normalised transaction dataset built from raw/
static/         the official implementation, extracted from the vendor bundle
ground_truth/   the ANSWER KEY - sealed, see below
benchmark/      blind datasets + frozen predictions
benchmark_gapclosed/  the same datasets plus the 0x83 collection-gap frames
results/        scores, verification, open-command analysis
```

The calibration gate (playbook v4 SS3) is **green** as of 2026-08-24; see
`TICKET26_CALIBRATION_GATE_RESULT.md` and the pre-registration it was run under,
`PRE_REGISTRATION_TICKET26.md`.

The separation between `raw/` + `observations/` and `ground_truth/` is load
bearing. Nothing under `raw/` or `observations/` carries a decoded field name;
semantics live only in `ground_truth/truth.json`, which exists so it can be
withheld from an inference run and opened afterwards to score it.

## Sources in the corpus

| file | source | frames | notes |
|---|---|---|---|
| `raw/physical_init_20260822.jsonl` | real device | 32 TX | full init sequence, byte-complete, captured with a hook installed before the app connected. RX side missed on this run. |
| `raw/physical_device_settings_20260822.jsonl` | real device | 63 | the Phase-1 read/set/readback/rollback cycles, with UI before/after. **Prefix-truncated to 8 bytes** — recorded as `hex_prefix` with `hex_full: null`, never reconstructed. |
| `raw/physical_macro_assignment_20260823.jsonl` | real device | 5 TX | the macro-assignment session. Contains the only captures of `0x83` operating on a **single** item (two-byte body) — the frames that settle its record stride. Two of its requests were composed by the capture session from the vendor bundle's serializer; the replies are genuine. |
| via `pdevemu.ground_truth` | real device | 29 pairs | byte-complete TX **and** RX. The only source with real reply bodies. |
| `reports/oracle/aula_web/HERO_84_HE/raw_full_trace.jsonl` | emulator | 2425 | official JS driving the fake WebHID device. |

`observations/transactions.jsonl` merges all of it: **2519 transactions, 2484
with a reply.**

## The emulator caveat, quantified

An emulator-backed corpus teaches you request structure faithfully and reply
structure only as far as someone pre-loaded it. Measured echo rates — a reply
byte-identical to its request carries no information:

| opcode | emulator | real device |
|---|---|---|
| `0x82` | 93/1255 echo (7%) | 1/7 |
| `0x83` | 0/490 echo | 0/10 |
| `0x84` | **21/21 echo (100%)** | 2/7 |
| `0x93` `0x95` `0x96` `0x99` | **100% echo** | never captured |

So the 410 emulator frames for the magnetic read groups and for `0x84` teach
nothing about what those replies contain — worse, they actively teach a false
"this command is an echo-ack write". The frozen inference run fell for exactly
that and labelled `0x93`/`0x95`/`0x96`/`0x99` as writes.

Treat emulator replies as absent evidence unless the emulator was seeded from a
real capture of that specific command.

## Rebuilding

```bash
python -m miner.inference.build_corpus     # raw/ + external sources -> observations/
python -m miner.inference.blind            # observations/ -> benchmark/ (3 modes)
python -m miner.inference.gap_closure      # benchmark/ + 0x83 gap -> benchmark_gapclosed/
python -m miner.inference.freeze  --engine engine_v3 --label <name>   # -> frozen_<name>/
python -m miner.inference.score_v3 --label <name>                     # -> results/scores_<name>.json
python -m miner.inference.unknowns         # what is still open, and what to try next
```

`score_v3.py` re-checks the manifest against its own sha256 digest, every hashed
engine and scorer source, the datasets by recorded path, and the frozen
predictions, and refuses to open `ground_truth/truth.json` on any drift. It has
no `--allow-drift`.

The older `run_benchmark.py` / `score.py` pair still reproduces the v1 baseline
under `benchmark/frozen/`. It hashes five engine files but not `reconstruct.py`,
not the datasets and not the scorer, and `score.py` accepts `--allow-drift`;
prefer `freeze` + `score_v3` for anything that is meant to be a gate submission.

**Rebuilding the corpus no longer reproduces `benchmark/`.** The upstream
emulator trace has grown from 2425 frames to 4324 since 2026-08-22 and nothing
pinned its hash, so `build_corpus` now yields 4418 transactions rather than
2519. The frozen datasets under `benchmark/` are the artifact of record and are
hashed in every manifest; treat `observations/transactions.jsonl` as a
regenerable intermediate, not as the corpus.
