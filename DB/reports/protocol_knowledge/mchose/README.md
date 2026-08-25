# MCHOSE protocol corpus

Second brand after `aula-bytech`, and the first meant to go through the
pipeline rather than through hand work. Acquired by TICKET-23 on 2026-08-24.

**Nothing here has been sent to any device.** No MCHOSE hardware exists in this
project; everything below is vendor artifact.

## The target

| | |
|---|---|
| Storefront entry | `https://www.mchose.store/pages/mchose-hub` |
| **M HUB Web** | `https://www.mchose.com.cn/` — Vite/Vue WebHID SPA |
| Secondary "Web Driver" | `https://www.mchose.com.cn:9999/` — **did not respond** on 2026-08-24 |
| Bundle self-report | `当前版本号: c68ce3684`, `提交时间: 2026-08-24 10:26` |
| Config centre | `https://cdn.mchose.com.cn/configCenter/` — `webVersion v3.15.2`, `pcVersion 1.2.04`, `rendererVersion 1.1.17` |
| Sourcemaps | none published (404) |

## The layer a static walk cannot see

The device knowledge is **not in the JS bundle**. M HUB Web fetches its
catalogue, per-device presets and firmware manifests from the config centre at
runtime, and each resource carries its own content hash in `global.json`'s
`version` map. That is why acquisition has two halves, and why both are kept:

```
acquisition/m_hub_web_manifest.json    35 artifacts, 12.7 MB — static module-graph closure
acquisition/observed_network.json      48 URLs — what a real browser session actually fetched
acquisition/config_center_manifest.json  the runtime layer, with per-resource hashes
acquisition/device_catalog.json        identity triples found in the bundle
acquisition/merged_catalog.json        all sources merged, with provenance and discrepancies
```

Grepping the entry bundle for asset paths yields 6 references; the static
closure is 35; the observed set adds the whole config centre on top. Any one of
them alone under-reports the corpus.

## What the catalogue says

* **77 distinct `vid:pid`** across five VIDs: `0x3837` (59), `0x41E4` (14),
  `0x5253` (2), `0x291D` (1), `0x0E6A` (1).
* `0x41E4` carries **both** keyboards and headsets. VID is not a family
  boundary, not even approximately.
* Every carded device has **two identities on different product ids**:
  `0x0001:0x0000` (the keyboard itself) and `0xff00:0x0001` (the vendor config
  collection). `aula-bytech` put its config collection on the *same* pid and
  used `0xFF60:0x0061`, and its `0xFF00:0x0001` was explicitly *not* the config
  channel. Nothing transfers between the two families here.
* `cardList` entries carry `webdriverEnum: {deviceType, subType}` — a candidate
  family discriminator, observed at one value only, so far.
* `keyboardPreset` holds 79 presets over **28 distinct keyboard `vid:pid`**,
  keyed `vid_pid:firmwareVersion` — presets vary by firmware, not just by model.

### The open edge

**27 keyboard `vid:pid` are known without names.** The config centre's keyboard
sources are id-keyed and carry no marketing names; the storefront lists names
and no ids. Nothing in the software artifacts bridges them, so no `vid:pid` here
may be stated to *be* a named product. Closed by observing what the app calls a
connected device — TICKET-25, not string similarity.

The storefront/artifact discrepancy counts in `merged_catalog.json` are **not**
evidence that the vendor overstates support: for keyboards they are mostly this
missing edge showing through.

## Rebuilding

```bash
cd DB/protocol-miner
python -m miner.static.mchose_version_check  --bundle-manifest ... --configcenter-manifest ...   # gate first
python -m miner.static.mchose_acquire        --out <scratch> --manifest ...
python -m miner.static.mchose_live_assets    --out ...
python -m miner.static.mchose_configcenter   --out <scratch> --manifest ...
python -m miner.static.mchose_catalog        --blobs <scratch>/blobs --out ...
python -m miner.static.mchose_merge_catalog  --bundle-catalog ... --configcenter <scratch> --out ...
```

`mchose_version_check` exits 2 if MCHOSE has redeployed since the manifests were
written; run it before trusting anything else here. `aula-bytech` redeployed
twice in six days and both times it was noticed only after a finding had been
published against a build that no longer existed.

### It fired on day one — 2026-08-25

```text
config `mouseFirmwareHistory`: recorded '80857e6172', live 'c470c759a5'
config `otaConfigApp_prod`:    recorded '57b321aae9', live '1f107cd691'
config `pre-newMouseConfig`:   recorded 'b35f0e4fe0', live 'd2a881f80c'
```

Worth reading for what is **absent** from that list. The bundle's own version and
`webVersion.hash` did not move, and neither did `cardList`, `keyboardConfig` or
`keyboardPreset`. So TICKET-24's codec findings and TICKET-25's factory-reset
trace — all of which rest on the bundle and the keyboard sources — are about a
build still being served, while the three resources that did move are mouse
firmware and OTA metadata, which this project has deliberately not investigated.

That precision is the reason the checker compares **every** axis rather than one.
A single bundle-hash check would have reported "no change" and been useless; a
coarse "something moved" check would have invalidated findings that are fine.

## What is deliberately absent

* **Vendor blobs.** `data/README.md` forbids committing vendor artifacts
  anywhere in this repository. The manifests carry hashes and provenance; the
  bytes live in a scratchpad outside the repo.
* **Windows drivers.** ~15 installer links (Google Drive / lanzouj) are
  inventoried, none downloaded: executables from an untrusted source need
  explicit authorisation, and they are input to `tools/ingest`, a separate
  workspace.
* **Any decode.** Which chunk holds the wire codec is recorded as a finding for
  TICKET-24 (`purify.es-BGo9zI_u.js`, 7.7 MB, misleadingly named — 20 ×
  `sendReport`, 2587 × `MOUSE`, 84 × `KEYBOARD`; the 2.3 MB `theme-*.js` has the
  feature model but no wire code at all). Nothing is decoded here.

Family boundaries and scope: `docs/decisions/0003-mchose-family-boundaries.md`.
