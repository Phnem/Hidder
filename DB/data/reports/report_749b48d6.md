# Peripheral Registry Ingestion Report — Run `749b48d6`

- **Started At**: 2026-08-19T09:56:36.765453+00:00
- **Duration**: 1:19:33
- **Brands Crawled**: 100
- **Total Products in Registry**: 6170 (+6170 new)
- **Hardware Devices**: 2018
- **Stored CAS Artifacts**: 189 (1784.99 MB)
- **Artifacts Discovered**: 246
- **Artifacts Downloaded**: 188
- **Cache Hits (Pre-download)**: 0
- **Conditional 304 Hits**: 0
- **Duplicate URLs Skipped**: 16
- **Large Artifacts Deferred**: 21
- **Bytes Downloaded**: 1791.30 MB
- **Bytes Avoided by Cache**: 0.00 MB
- **Discovered VID/PIDs**: 37
- **Protocol Hints**: 59

## Reliability & Error Metrics

- **Fatal Errors**: 0
- **Collector Errors**: 0
- **Artifact Download Failures**: 21
- **Parse Failures**: 0
- **Warnings**: 52

## Storage & Invariant Integrity Audit

- **SQLite Database File Size**: 8.45 MB (8,855,552 bytes)
- **Referenced CAS Artifacts Total Size**: 1784.99 MB (1,871,699,255 bytes)
- **Exact Duplicate Identity Groups**: 0
- **VID/PID Identifiers with NULL Provenance**: 0
- **Protocol Hints with NULL Provenance**: 0
- **Technical Facts with NULL Provenance**: 0

## Ecosystem Provenance Chains (5 Canonical Audits)

### AULA Provenance Chain

1. **Source URL**: `https://hub.aulastar.com/config/devices.json`
2. **Artifact**: `aula_hub_devices.json` (`6d48bb7bddc68dbdfa879beee960a220624810a7a5829654d9634799926e98a0`)
3. **Parsed Structured Record**: Model `AULA HERO 84 HE Mechanical Keyboard`
4. **Correlated Product**: Product #1 **HERO 84 HE** (Category: `keyboard`)
5. **Resulting Technical Evidence**:
   - VID: `0x372E` | PID: `0x103E` (UsagePage: 65376, Usage: 97)
   - Protocol Hints: `pollingRateMax = 8000`, `sdkModuleName = bytech`

### ATK Provenance Chain

1. **Source URL**: `https://hub.atk.pro/assets/devices.json`
2. **Artifact**: `atk_v_hub_devices.json` (`80fc808bd67b2a2c0c38aa31550da3082bac5551b1322402c7a681653bd5ee84`)
3. **Parsed Structured Record**: Model `ATK Blazing Sky F1 Wireless Mouse`
4. **Correlated Product**: Product #37 **Blazing Sky F1** (Category: `mouse`)
5. **Resulting Technical Evidence**:
   - VID: `0x3554` | PID: `0xF101` (UsagePage: 65376, Usage: 97)
   - Protocol Hints: `pollingRateMax = 8000`, `sdkModuleName = vgn_atk_hub`, `sensor = PAW3950`

### EPOMAKER Provenance Chain

1. **Source URL**: `https://epomaker.com/config/driver_devices.json`
2. **Artifact**: `epomaker_driver_definitions.json` (`1de70f3bcc9afae5f06d1cc5fd21caaca7f33d504166d76674e1e0e652a23a4d`)
3. **Parsed Structured Record**: Model `EPOMAKER RT100 Retro Keyboard`
4. **Correlated Product**: Product #53 **RT100** (Category: `keyboard`)
5. **Resulting Technical Evidence**:
   - VID: `0x3151` | PID: `0x1001` (UsagePage: 65376, Usage: 97)
   - Protocol Hints: `sdkModuleName = epomaker_driver`

### Keychron Provenance Chain

1. **Source URL**: `https://launcher.keychron.com/config/definitions.json`
2. **Artifact**: `keychron_launcher_definitions.json` (`0e6611fae460960d0fe3bb8c66ff6eaada4a326904f391b72ccdba9e12ff240c`)
3. **Parsed Structured Record**: Model `Keychron Q1 Max`
4. **Correlated Product**: Product #177 **Q1 Max** (Category: `keyboard`)
5. **Resulting Technical Evidence**:
   - VID: `0x3434` | PID: `0x0101` (UsagePage: 65376, Usage: 97)
   - Protocol Hints: `sdkModuleName = qmk_via`

### KBDfans Provenance Chain

1. **Source URL**: `https://kbdfans.com/products.json?limit=250`
2. **Artifact**: `ydkb_kbdfans_agar_micro_via.json` (`0e018d224bb58915592cc5dc9608fb9b0466fe89d59a3cf0fd42ab37e4bd82a3`)
3. **Parsed Structured Record**: Model `Agar Micro`
4. **Correlated Product**: Product #3242 **Agar Micro** (Category: `keyboard`)
5. **Resulting Technical Evidence**:
   - VID: `0x9D5B` | PID: `0x2510` (UsagePage: None, Usage: None)
   - Protocol Hints: None

## Brand Discovery Status Summary

| # | Brand | Status | Products | Devices | Artifacts | VID/PIDs | Hints | Tech Evidence Products | Blocking Reason |
|---|-------|--------|----------|---------|-----------|----------|-------|------------------------|-----------------|
| 1 | **A4Tech** | `METADATA_ONLY` | 19 | 6 | 0 | 0 | 0 | 0 |  |
| 2 | **ARDOR GAMING** | `METADATA_ONLY` | 1 | 0 | 0 | 0 | 0 | 0 |  |
| 3 | **ASUS ROG** | `SUPPORTED_FULL` | 1 | 0 | 1 | 0 | 0 | 0 |  |
| 4 | **ATK** | `SUPPORTED_FULL` | 11 | 11 | 3 | 11 | 28 | 11 |  |
| 5 | **AULA** | `SUPPORTED_FULL` | 36 | 36 | 3 | 8 | 10 | 8 |  |
| 6 | **Ajazz** | `SUPPORTED_FULL` | 29 | 23 | 5 | 0 | 0 | 0 |  |
| 7 | **Akko** | `SUPPORTED_FULL` | 35 | 21 | 17 | 0 | 0 | 0 |  |
| 8 | **Alienware** | `METADATA_ONLY` | 1 | 0 | 0 | 0 | 0 | 0 |  |
| 9 | **Attack Shark** | `METADATA_ONLY` | 173 | 73 | 0 | 0 | 0 | 0 |  |
| 10 | **Bloody** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 11 | **CHERRY** | `METADATA_ONLY` | 9 | 2 | 0 | 0 | 0 | 0 |  |
| 12 | **Chilkey** | `METADATA_ONLY` | 54 | 17 | 0 | 0 | 0 | 0 |  |
| 13 | **Chosfox** | `METADATA_ONLY` | 229 | 41 | 0 | 0 | 0 | 0 |  |
| 14 | **Cidoo** | `METADATA_ONLY` | 12 | 11 | 0 | 0 | 0 | 0 |  |
| 15 | **Cooler Master** | `METADATA_ONLY` | 31 | 2 | 0 | 0 | 0 | 0 |  |
| 16 | **Corsair** | `METADATA_ONLY` | 2 | 2 | 0 | 0 | 0 | 0 |  |
| 17 | **Cougar Gaming** | `BLOCKED_WAF` | 0 | 0 | 0 | 0 | 0 | 0 | HTTP 403 WAF / anti-bot protection |
| 18 | **Dareu** | `SUPPORTED_FULL` | 52 | 37 | 13 | 0 | 0 | 0 |  |
| 19 | **Dark Project** | `METADATA_ONLY` | 18 | 1 | 0 | 0 | 0 | 0 |  |
| 20 | **Darmoshark** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 21 | **Delux** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 22 | **Drop** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 23 | **DrunkDeer** | `METADATA_ONLY` | 12 | 4 | 0 | 0 | 0 | 0 |  |
| 24 | **Ducky** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 25 | **E-Yooso** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 26 | **EPOMAKER** | `SUPPORTED_FULL` | 124 | 57 | 2 | 7 | 7 | 7 |  |
| 27 | **Endgame Gear** | `METADATA_ONLY` | 34 | 12 | 0 | 0 | 0 | 0 |  |
| 28 | **FL·ESPORTS** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 29 | **Fantech** | `METADATA_ONLY` | 245 | 93 | 0 | 0 | 0 | 0 |  |
| 30 | **Feker** | `SUPPORTED_FULL` | 5 | 5 | 3 | 0 | 0 | 0 |  |
| 31 | **Filco** | `BLOCKED_WAF` | 0 | 0 | 0 | 0 | 0 | 0 | HTTP 403 WAF / anti-bot protection |
| 32 | **Finalmouse** | `METADATA_ONLY` | 38 | 38 | 0 | 0 | 0 | 0 |  |
| 33 | **Fnatic Gear** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 34 | **G-Wolves** | `METADATA_ONLY` | 66 | 40 | 0 | 0 | 0 | 0 |  |
| 35 | **Gamakay** | `SUPPORTED_FULL` | 66 | 27 | 4 | 0 | 0 | 0 |  |
| 36 | **Glorious** | `SUPPORTED_FULL` | 226 | 31 | 1 | 0 | 0 | 0 |  |
| 37 | **HHKB** | `SOFTWARE_ONLY` | 0 | 0 | 4 | 0 | 0 | 0 | No product listings found on catalog page |
| 38 | **HyperX** | `METADATA_ONLY` | 232 | 80 | 0 | 0 | 0 | 0 |  |
| 39 | **IO by Red Square** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 40 | **IQUNIX** | `METADATA_ONLY` | 12 | 2 | 0 | 0 | 0 | 0 |  |
| 41 | **IROK** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 42 | **Incott** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 43 | **KBDfans** | `SUPPORTED_FULL` | 437 | 115 | 2 | 2 | 0 | 2 |  |
| 44 | **Kemove** | `METADATA_ONLY` | 12 | 9 | 0 | 0 | 0 | 0 |  |
| 45 | **Keychron** | `SUPPORTED_FULL` | 243 | 160 | 4 | 9 | 14 | 9 |  |
| 46 | **Keycult** | `METADATA_ONLY` | 38 | 11 | 0 | 0 | 0 | 0 |  |
| 47 | **Kysona** | `METADATA_ONLY` | 8 | 3 | 0 | 0 | 0 | 0 |  |
| 48 | **Kzzi** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 49 | **Lamzu** | `METADATA_ONLY` | 70 | 27 | 0 | 0 | 0 | 0 |  |
| 50 | **Lemokey** | `METADATA_ONLY` | 126 | 21 | 0 | 0 | 0 | 0 |  |
| 51 | **Leobog** | `SUPPORTED_FULL` | 14 | 6 | 4 | 0 | 0 | 0 |  |
| 52 | **Leopold** | `METADATA_ONLY` | 11 | 0 | 0 | 0 | 0 | 0 |  |
| 53 | **Lin Works** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 54 | **Logitech G** | `BLOCKED_WAF` | 0 | 0 | 0 | 0 | 0 | 0 | HTTP 403 WAF / anti-bot protection |
| 55 | **MCHOSE** | `METADATA_ONLY` | 39 | 26 | 0 | 0 | 0 | 0 |  |
| 56 | **MSI** | `SOFTWARE_ONLY` | 0 | 0 | 0 | 0 | 0 | 0 | No product listings found on catalog page |
| 57 | **Machenike** | `METADATA_ONLY` | 137 | 68 | 0 | 0 | 0 | 0 |  |
| 58 | **Madlions** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 59 | **Matrix Lab** | `METADATA_ONLY` | 34 | 1 | 0 | 0 | 0 | 0 |  |
| 60 | **MelGeek** | `METADATA_ONLY` | 103 | 52 | 0 | 0 | 0 | 0 |  |
| 61 | **Meletrix** | `METADATA_ONLY` | 206 | 88 | 0 | 0 | 0 | 0 |  |
| 62 | **Mode Designs** | `METADATA_ONLY` | 76 | 0 | 0 | 0 | 0 | 0 |  |
| 63 | **MonsGeek** | `SUPPORTED_FULL` | 61 | 41 | 13 | 0 | 0 | 0 |  |
| 64 | **NZXT** | `SUPPORTED_FULL` | 128 | 29 | 1 | 0 | 0 | 0 |  |
| 65 | **Ninjutso** | `SUPPORTED_FULL` | 8 | 4 | 2 | 0 | 0 | 0 |  |
| 66 | **NuPhy** | `METADATA_ONLY` | 29 | 19 | 0 | 0 | 0 | 0 |  |
| 67 | **Phylina** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 68 | **Pulsar Gaming Gears** | `SUPPORTED_FULL` | 240 | 96 | 68 | 0 | 0 | 0 |  |
| 69 | **Qwertykeys** | `METADATA_ONLY` | 106 | 69 | 0 | 0 | 0 | 0 |  |
| 70 | **Rapoo** | `METADATA_ONLY` | 1 | 0 | 0 | 0 | 0 | 0 |  |
| 71 | **Rawm** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 72 | **Razer** | `METADATA_ONLY` | 11 | 5 | 0 | 0 | 0 | 0 |  |
| 73 | **Realforce** | `METADATA_ONLY` | 2 | 0 | 0 | 0 | 0 | 0 |  |
| 74 | **Red Square** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 75 | **Royal Kludge** | `SUPPORTED_FULL` | 54 | 47 | 5 | 0 | 0 | 0 |  |
| 76 | **Scyrox** | `METADATA_ONLY` | 12 | 6 | 0 | 0 | 0 | 0 |  |
| 77 | **Sikakeyb** | `METADATA_ONLY` | 38 | 29 | 0 | 0 | 0 | 0 |  |
| 78 | **Skyloong** | `BLOCKED_WAF` | 0 | 0 | 0 | 0 | 0 | 0 | HTTP 403 WAF / anti-bot protection |
| 79 | **SteelSeries** | `METADATA_ONLY` | 1 | 0 | 0 | 0 | 0 | 0 |  |
| 80 | **TGR** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 81 | **Tecware** | `SOFTWARE_ONLY` | 0 | 0 | 33 | 0 | 0 | 0 | No product listings found on catalog page |
| 82 | **Thunderobot** | `BLOCKED_WAF` | 0 | 0 | 0 | 0 | 0 | 0 | HTTP 403 WAF / anti-bot protection |
| 83 | **Turtle Beach** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 84 | **VAXEE** | `BLOCKED_WAF` | 0 | 0 | 0 | 0 | 0 | 0 | HTTP 403 WAF / anti-bot protection |
| 85 | **VGN** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 86 | **VXE** | `METADATA_ONLY` | 5 | 4 | 0 | 0 | 0 | 0 |  |
| 87 | **Varmilo** | `METADATA_ONLY` | 42 | 15 | 0 | 0 | 0 | 0 |  |
| 88 | **WLMOUSE** | `METADATA_ONLY` | 65 | 35 | 0 | 0 | 0 | 0 |  |
| 89 | **WOBKEY** | `METADATA_ONLY` | 30 | 11 | 0 | 0 | 0 | 0 |  |
| 90 | **Waizowl** | `METADATA_ONLY` | 14 | 1 | 0 | 0 | 0 | 0 |  |
| 91 | **Weikav** | `BLOCKED_WAF` | 0 | 0 | 0 | 0 | 0 | 0 | HTTP 403 WAF / anti-bot protection |
| 92 | **Womier** | `SUPPORTED_FULL` | 132 | 31 | 1 | 0 | 0 | 0 |  |
| 93 | **Wooting** | `METADATA_ONLY` | 4 | 1 | 0 | 0 | 0 | 0 |  |
| 94 | **Wuque Studio** | `METADATA_ONLY` | 260 | 34 | 0 | 0 | 0 | 0 |  |
| 95 | **X-Bows** | `METADATA_ONLY` | 13 | 7 | 0 | 0 | 0 | 0 |  |
| 96 | **Xinmeng** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
| 97 | **YMDK** | `METADATA_ONLY` | 1081 | 70 | 0 | 0 | 0 | 0 |  |
| 98 | **Yunzii** | `METADATA_ONLY` | 485 | 187 | 0 | 0 | 0 | 0 |  |
| 99 | **ZOWIE** | `METADATA_ONLY` | 21 | 18 | 0 | 0 | 0 | 0 |  |
| 100 | **Zaopin** | `NO_OFFICIAL_CATALOG_FOUND` | 0 | 0 | 0 | 0 | 0 | 0 | No products or software packages discovered at official endpoints |
