# Peripheral (`peripheral-core`) — Master Plan

## Workflow

Current workflow state: READY_FOR_IMPLEMENTATION
Current ticket: None
Last completed ticket: TICKET-11 (`psafety` — ACL, `SafeCommandId`, rate limiter, journal)
Next eligible ticket: **TICKET-10** (`pregistry` + многосигнальный fingerprinting). Параллельно доступен TICKET-05 (research track).
Last updated: 2026-08-18

Реализация начата 2026-08-17 (пользователь: «раздели план на фазы и начинай фазу 1»). Phase 1 по разбивке ниже — документационные тикеты (03/01) + инфраструктурный скелет (07).

## Execution phases

Разбивка 21 тикета на исполнительные фазы и два параллельных трека. Фазы — про порядок работы в этой сессии/сессиях; нумерация НЕ совпадает с «Phase 0..6» исходного плана (там фазы продукта). Соответствие указано в колонке «План».

| Фаза | Содержание | Тикеты | План | Входной гейт |
|---|---|---|---|---|
| **1. Знание + фундамент** | Prior-art карта, конспект ROYUAN-протокола, изучение методов sharkfin, cargo workspace + Tauri-скелет + CI + `git init` | 03, 01, 21, 07 | Phase 0 + начало Phase 1 | нет (07 — Rust toolchain на машине) |
| **2. AULA Hardware Reality** | Реальный HID-инвентарь единственной reference board | **08 (DONE)** | Phase 0/1 | 07 (выполнен) |
| **3. Read-only вертикальный срез** | `psafety`-скелет, fingerprinting, первый protocol engine (AULA read-only), Tauri UI, эмулятор+CI-тест | **11 → 10 → 12 → 13 → 14** | Phase 1 DoD (AC2) | 07, 08 (оба выполнены) |
| **4. Запись и аналог** | Verified write + Analog Monitor, Learning Mode | 15, 16 | Phase 2/3 | фаза 3 целиком |
| **5. Второе семейство + релиз** | Second protocol family (какое именно — решают данные, не план), профили, ingestion pipeline, tray-оболочка | 17, 18, 20 (shell-трек) | Phase 3/4 | 15 |
| **6. Мыши и wireless** | Мышиный слой, реальные battery-данные в трее | 19, 20 (data-трек) | Phase 6 | 17 |
| **R. Research track** (параллельный, ничего не блокирует) | OEM/platform-карта для будущего fingerprinting | 05 | Phase 0 | — |
| **V. Remote external validation** (параллельный, capability-гейт) | Удалённый inventory и идентификация EPOMAKER через обычную сборку у стороннего владельца | 09 → 06 | сквозной | пользовательская сборка с безопасным Export (см. ниже) |

### Phase 2 — AULA Hardware Reality (перестроена дважды 2026-08-17)

**Главный hardware-gate фазы уже пройден TICKET-08.** Первый настоящий инвентарь снят, `ptransport` работает на Windows на реальном железе.

Фаза перестраивалась в этот день дважды. Первый раз — отмена покупки ROYUAN-платы (EPOMAKER становится вторым референсным устройством). Второй раз — выяснилось, что **EPOMAKER физически находится не у разработчика, а у стороннего владельца (отца пользователя)**. Прогнать по ней developer-инструменты нельзя и не нужно: это потребовало бы установки Rust/cargo/Build Tools/USBPcap/скриптов на чужой компьютер, что противоречит цели продукта и не даёт product-level валидации.

Состав фазы после второй перестройки:

```text
Phase 2 — AULA Hardware Reality
└── TICKET-08  DONE — реальный HID inventory AULA Hero 84 HE   ← hardware gate пройден
                      это весь состав фазы

Research track (параллельно, ничего не блокирует)
└── TICKET-05  OEM/platform map для будущего fingerprinting

Remote external validation track (параллельно, capability-гейт)
└── TICKET-09 → TICKET-06   EPOMAKER, удалённо, через обычную сборку
```

**TICKET-05 вынесен в параллельный research track и не является входным гейтом Phase 3.** Данные, нужные конкретно TICKET-10, уже добываются из AULA. Держать реализацию в ожидании большой OEM-таблицы запрещено.

**Phase 2 считается закрытой.** Единственный её тикет (08) — DONE_WITH_DEVIATIONS; 05 перенесён в research track, 06 и 09 — в remote-validation track, все три с записью в `EXECUTION_LOG.md`.

### EPOMAKER — remote external validation device

EPOMAKER **больше не reference board**. Её новая роль: **remote external validation device** — поздняя проверка уже более-менее готового продукта на неизвестном устройстве у постороннего человека.

Что она будет проверять одновременно:

- generic `ptransport` — справляется ли он с устройством, которого не видел ни один разработчик;
- `pregistry` и fingerprinting на втором реальном устройстве;
- безопасный Inventory/Export flow целиком;
- редактирование чувствительных полей (серийный номер) по умолчанию;
- поведение программы на неизвестном устройстве;
- отсутствие AULA-специфичных предположений в коде;
- будущий Learning Mode Level 0;
- удобство сценария для обычного пользователя без dev-toolchain.

**Заранее не назначать** EPOMAKER: вторым protocol family, HE-клавиатурой, ROYUAN, конкретным OEM, wireless-девайсом. Всё это устанавливается только по данным — спецификация и так требует многосигнального определения family, а не вывода по бренду или VID:PID.

Сценарий:

```text
владелец устройства
        ↓ скачивает обычную сборку Peripheral
        ↓ запускает без Rust / dev-toolchain
        ↓ подключает EPOMAKER
Peripheral обнаруживает устройство
        ↓ Export Device Report / Export .heprofile
файл передаётся разработчику
        ↓ по нему + публичным источникам
модель / OEM / тип / возможности
```

**Гейт возврата — capability-based, а не «после фазы N».** EPOMAKER разрешено тестировать, когда существует обычная Windows-сборка, которая умеет:

1. безопасно enumerate неизвестное устройство;
2. ничего в него не писать;
3. генерировать стандартный versioned inventory/profile-артефакт;
4. автоматически редактировать серийник и прочие идентификаторы, не нужные для fingerprinting;
5. показывать пользователю одну понятную кнопку **Export**.

Наиболее естественная точка — после TICKET-13/14 либо как безопасный Level 0 внутри TICKET-16. Форсировать тест сразу после TICKET-13 не следует: пользователь хочет проверять EPOMAKER на «более-менее готовом продукте», а не на инженерном debug-инструменте. Лучше дождаться сборки, которую не стыдно дать другому человеку.

**Архитектурное правило, которое нельзя нарушить ради этих данных:** EPOMAKER проходит через тот же production pipeline, что и AULA. Никакого `if epomaker { ... }` в транспортном слое ради получения инвентаря. Если generic `ptransport` не справится — это находка о generic transport abstraction, и чинится абстракция/транспорт, а не создаётся vendor-specific enumeration.

Четыре возможных исхода, и **ни один не считается «провалом EPOMAKER-теста»**:

| Исход | Что это даёт |
|---|---|
| A. Другое HE-семейство | отличный кандидат в second engine |
| B. То же или родственное семейство | cross-device validation fingerprinting'а |
| C. Не HE | generic HID/keyboard validation транспорта и UI |
| D. Нет доступного config-интерфейса | negative compatibility case: приложение обязано сказать «Config interface unavailable», а не писать наугад |

### Архитектурный чекпоинт — повестка (findings TICKET-08)

Чекпоинт — между фазой 3 и 4, и повторно перед 5 (нужен также для нерешённого вопроса «tray-процесс vs process watcher», `spec.md` SAFE_DEFAULT). **Ничего из перечисленного не исправляется сейчас — но всё формально записано и обязано быть рассмотрено:**

| Находка | Что рассмотреть | Где записана |
|---|---|---|
| `DeviceSession` vs реальность устройства | 1 периферия → 3 интерфейса → 7 TLC → несколько потенциально нужных handle'ов. Рассмотреть модель `DeviceSession owns CollectionHandleSet` или эквивалент. Инвариант «handles никогда не выходят из `ptransport`» сохраняется без изменений | `spec.md` FR10 (врезка) |
| Ошибки `hidapi` | Текстовые/локализованные сообщения недостаточны для machine-readable детекции stall'а → kill-switch FR3 на локализованной ОС не сработает. Win32 escape hatch **сейчас не вводится**; вернуться при первом настоящем I/O (TICKET-12/15) и только тогда решить, нужна ли platform-native классификация ошибок | `spec.md` FR7 (врезка), Known risks |
| `opened = true` | `descriptor/enumeration access` ≠ `usable read/write config channel`. Не повышать confidence на том основании, что коллекция открылась для метаданных | `spec.md` FR7 (врезка), Known risks |
| Serial privacy | Политика «`serial present: true` / `serial value: REDACTED` по умолчанию» стала **общей**, не AULA-специфичной. Сырой серийник — только по явному debug/export opt-in | `spec.md` § Domain rules + Test seams |

Правила перехода между фазами: фаза считается закрытой, когда все её тикеты имеют статус DONE/DONE_WITH_DEVIATIONS либо явно перенесены в другую фазу/трек с записью в `EXECUTION_LOG.md`. Тикет 02 (письмо автору sharkfin) — единственный, чьё завершение требует действия пользователя (отправка сообщения), поэтому в фазе 1 выполнен только черновик.

### Phase 3 — основная последовательность разработки (закреплена 2026-08-17)

После TICKET-08 основной code path:

```text
TICKET-11   psafety / SafeCommandId
     ↓
TICKET-10   multi-signal fingerprinting / pregistry
     ↓
TICKET-12   AULA read-only ProtocolEngine
     ↓
TICKET-13   Devices / HE / Journal UI
     ↓
TICKET-14   device emulator + replay/fingerprint tests
     ↓
TICKET-15   Verified Write + Analog Monitor
     ↓
TICKET-16   Learning Mode

TICKET-05 — параллельно, как research. Ничего не блокирует.
```

Почему именно такой порядок:

**TICKET-11 первым.** После TICKET-08 проект впервые приблизился к настоящему I/O. До появления первого protocol engine нужно закрепить архитектурную гарантию:

```text
raw opcode      ✗
SafeCommandId   ✓
Safety Gate     ✓
Transport
```

Ни один protocol engine не должен получить возможность случайно обойти safety boundary. Это дешевле сделать до engine, чем встраивать в уже написанный.

**Затем TICKET-10.** Теперь есть настоящие данные AULA — descriptor topology, TLC, VID/PID, strings, report IDs и размеры, release, vendor-коллекции. На них `pregistry` впервые строится не теоретически, а против реального устройства.

**Затем TICKET-12.** Первый AULA engine — строго read-only. Он обязан пользоваться `DeviceSession`, `pregistry`, capability-моделью и safety-границами, а не `hidapi` напрямую.

**Затем UI и эмулятор.** После появления реального вертикального среза имеет смысл строить нормальный пользовательский путь и эмуляцию — и именно тогда становится достижим capability-гейт remote-валидации EPOMAKER.

### Phase 1 status

| Тикет | Статус | Примечание |
|---|---|---|
| TICKET-03 | DONE | `docs/prior-art/inventory.md` |
| TICKET-01 | DONE_WITH_DEVIATIONS | `docs/prior-art/royuan.md`; исходники sharkfin не читались (только `PROTOCOL.md`) — обоснование в тикете |
| TICKET-21 | DONE | `docs/prior-art/sharkfin-methods.md`; заменил черновик TICKET-02 по решению пользователя |
| TICKET-02 | READY, отложен пользователем | Отправка сообщения третьему лицу требует явного разрешения; предмет обмена станет содержательнее после TICKET-08 |
| TICKET-07 | DONE_WITH_DEVIATIONS | Коммит `f770750`. Единственное отклонение: CI написан, но не исполнялся — нет remote-репозитория. |

**Фаза 1 закрыта** (2026-08-17). Не выполнен только TICKET-02, отложенный самим пользователем; он ничего не блокирует.

### Среда разработки (установлено 2026-08-17 с разрешения пользователя)

rustup 1.29.0, rustc/cargo 1.97.1 (`stable-x86_64-pc-windows-msvc`), clippy 0.1.97, rustfmt 1.9.0, cargo-deny 0.20.2, VS 2022 Build Tools 17.14.37 (workload VCTools), Node 24.15.0, npm 11.12.1, git 2.55.0, Python 3.14.5, WebView2 151.0.4129.86.

Замечание на будущее: при установке VS Build Tools через winget параллельно с другой winget-установкой инсталлятор падает с exit 5008. Ставить по одному.

## Goal

Локальный кроссплатформенный конфигуратор для HE-периферии (клавиатуры в первую очередь), позиционируемый HE-first в маркетинге и universal в архитектуре (`peripheral-core`). Полная формулировка — `spec.md`.

## Canonical specification

`.scratch/peripheral-configurator/spec.md`

## Architecture review

`.scratch/peripheral-configurator/architecture/INITIAL_REVIEW.md`

## Handoff

`.scratch/peripheral-configurator/CURRENT_HANDOFF.md` (создан 2026-08-17 — до начала реализации отсутствовал)

## Global constraints

- **Лицензия:** proprietary app, permissive-only зависимости (MIT/Apache-2.0/BSD/ISC), GPL/AGPL/LGPL запрещены в шипящемся бинаре. GPL-проекты (sharkfin, OpenRGB, OpenRazer) — только источник фактов, не кода. См. `docs/decisions/0001-license.md`.
- **Никакой прошивки (flash) устройства в v1** — ни при каких обстоятельствах, даже по запросу пользователя без явного расширения scope через отдельный ADR.
- **`tools/ingest` физически изолирован** от релизной сборки с первого коммита workspace.
- **Запись в устройство только через `SafeCommandId`** — сырые опкоды никогда не достигают транспорта в production-сборке.
- **`confidence < Verified` → read-only**, без исключений и без «экспериментальных» контролов записи в обычном UI.
- Референсное железо: **AULA Hero 84 HE** — единственная reference board на машине разработчика. Обновлено 2026-08-17 дважды: покупка ROYUAN-платы отменена; EPOMAKER находится у стороннего владельца и переведена в **remote external validation track** (не reference board). **Принадлежность EPOMAKER к какому-либо protocol family не предполагается заранее** — её устанавливают данные удалённого инвентаря.
- **EPOMAKER проходит через тот же production pipeline, что и AULA.** Vendor-specific ветка enumeration (`if epomaker { ... }`) в транспортном слое запрещена; несовместимость чинится в абстракции транспорта.

## Non-goals

Полный список — `spec.md` § Out of scope. Кратко: RGB-канвас на весь ПК, свой ядерный/фильтр-драйвер Windows, клауд/аккаунты/обязательная телеметрия, firmware update/recovery (DEFERRED, не отменено навсегда).

## Verification commands

Установлены TICKET-07. Все перечисленные команды прогонялись локально на Windows; в CI те же команды разложены по джобам (`.github/workflows/ci.yml`), но сам CI ещё ни разу не исполнялся — remote-репозитория нет.

### Fast checks

```
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
python scripts/check_crate_dag.py
```

Фронтенд (из `app/`): `npm run typecheck`.

### Ticket checks

```
cargo build --workspace
cargo deny --all-features check licenses bans sources
```

Изоляция `tools/ingest` (три независимых проверки, см. джобу `isolation` в CI): `cargo metadata` не содержит `pingest`; сборка workspace не производит артефакт `pingest*`; `cargo check` в `tools/ingest` проходит отдельно.

Per-ticket verification plans — в каждом `issues/NN-*.md`.

### Full checks

Из `app/`: `npx vite build`, затем `npx tauri dev` — приложение должно подняться с окном «Peripheral»; повторный запуск бинаря не должен создавать второй процесс.

Hardware-in-the-loop чеклист ведётся на AULA Hero 84 HE (единственная плата на машине разработчика). Удалённая часть чеклиста (EPOMAKER) исполняется не разработчиком, а владельцем устройства через обычную сборку — см. TICKET-09.

## Ticket overview

| ID | Title | Status | Blocked by | Commit | Review |
|---|---|---|---|---|---|
| TICKET-01 | Изучить протокол sharkfin/ROYUAN | **DONE_WITH_DEVIATIONS** | — | — | самопроверка, блокеров нет |
| TICKET-02 | Связаться с автором sharkfin | READY (черновик; отправка — за пользователем) | TICKET-01 (done) | — | — |
| TICKET-03 | Инвентаризация prior art | **DONE** | — | — | самопроверка, блокеров нет |
| TICKET-04 | Лицензия проекта (ADR-0001) | **DONE** | — | — | — |
| TICKET-05 | Карта платформ + карточки наших устройств | READY — **research track** (ничего не блокирует) | TICKET-01 (done), TICKET-03 (done) | — | — |
| TICKET-06 | Удалённая идентификация EPOMAKER | **DEFERRED_REMOTE_VALIDATION** | capability-гейт: сборка с безопасным Export; данные из TICKET-09 | — | — |
| TICKET-07 | Cargo workspace + Tauri skeleton + CI | **DONE_WITH_DEVIATIONS** | TICKET-04 (done) | `f770750` | самопроверка, блокеров нет |
| TICKET-08 | Windows HID inventory — AULA | **DONE_WITH_DEVIATIONS** | TICKET-07 (done) | `503070e` | самопроверка, блокеров нет |
| TICKET-09 | Remote EPOMAKER inventory validation | **DEFERRED_REMOTE_VALIDATION** | capability-гейт: сборка с безопасным Export (естественно — после 13/14 или ранний Level 0 из 16) | — | — |
| TICKET-10 | Multi-signal fingerprinting (`pregistry`) | **READY — следующий** | TICKET-08 (done), TICKET-07 (done), TICKET-11 (done) | — | — |
| TICKET-11 | `psafety` ACL + `SafeCommandId` skeleton | **DONE_WITH_DEVIATIONS** | TICKET-07 (done) | `34e786c` | самопроверка, блокеров нет |
| TICKET-12 | Первый protocol engine (AULA, read-only) | PENDING | TICKET-08 (done), TICKET-11, TICKET-10 | — | — |
| TICKET-13 | Tauri UI skeleton (Devices/HE/Journal) | PENDING | TICKET-07 (done), TICKET-12 | — | — |
| TICKET-14 | Device emulator + CI fingerprint-тест | PENDING | TICKET-08 (done), TICKET-10 | — | — |
| TICKET-15 | EPIC: Verified write + Analog Monitor | PENDING | TICKET-07..14 | — | — |
| TICKET-16 | EPIC: Learning Mode | PENDING | TICKET-15 | — | — |
| TICKET-17 | EPIC: second protocol family + профили + релиз v0.1 | PENDING | TICKET-15 (блокировка на TICKET-09 снята) | — | — |
| TICKET-18 | EPIC: Ingestion pipeline | PENDING | TICKET-10, TICKET-04 (done) | — | — |
| TICKET-19 | EPIC: Мыши | PENDING | TICKET-17 | — | — |
| TICKET-20 | EPIC: System Tray + `dev.power.*`/`dev.connection` | PENDING | TICKET-13 (shell); TICKET-19 (реальные battery-данные) | — | — |
| TICKET-21 | Изучить инженерные методы sharkfin по исходникам | **DONE** | — | — | самопроверка, блокеров нет |

Примечание по «нескольким READY одновременно»: TICKET-01, TICKET-03 и TICKET-07 не блокируют друг друга и могут выполняться в любом порядке или параллельно (разные типы работы — чтение/конспект vs инфраструктура). Правило «один тикет в основном дереве одновременно» (skill's "One ticket at a time") относится к *коду в общем working tree*; TICKET-01/02/03/05/06 — исследовательские/закупочные, не код, поэтому не конкурируют за одно и то же дерево с TICKET-07. При старте Phase 8 реализация всё равно ведётся по одному code-тикету за раз.

## Ticket details

Полные тикеты — `.scratch/peripheral-configurator/issues/01-*.md` … `19-*.md`. Ниже — только сводка для тикетов, уже имеющих статус, отличный от «ничего не сделано».

### TICKET-04 — Лицензия проекта (ADR-0001)

Status: DONE
Tracker reference: локальный (`issues/04-license-adr.md`)
Dependencies: нет
Acceptance criteria: все выполнены (см. issue-файл)
Implementation summary: `LICENSE` (proprietary placeholder) + `docs/decisions/0001-license.md` (полный ADR: контекст/варианты/решение/последствия) созданы, решение зафиксировано по итогам пользовательского интервью 2026-08-17.
Deviations: выполнено во время SPECIFICATION-фазы планирования, а не в рамках Phase 8 implementation loop — см. issue-файл, раздел Deviations, для полного обоснования. Не код продукта, riскnone.
Architecture notes: разблокировал формулировку правил заимствования (facts/code/docs) для всех Phase 0 тикетов и для `spec.md`.
Verification evidence: ручная — файлы существуют, содержание соответствует шаблону ADR (§19.2 плана) и решению пользователя.
Commit: не создавался (нет git-репозитория до TICKET-07; будет закоммичен вместе с первым коммитом workspace).
Follow-up tickets: TICKET-18 (ingestion pipeline) прямо ссылается на §7.3/§14-режим фактов/кода/доков, установленный этим решением.

### TICKET-03 — Инвентаризация prior art

Status: DONE (2026-08-17)
Tracker reference: локальный (`issues/03-prior-art-inventory.md`)
Dependencies: нет
Acceptance criteria: все выполнены
Implementation summary: `docs/prior-art/inventory.md` — 15 проектов, лицензия каждого проверена по странице репозитория 2026-08-17, режим использования (`code`/`facts`/`docs`) со ссылкой на ADR-0001, раздел о планируемой инкорпорации permissive-кода и его влиянии на дизайн.
Deviations: таблица шире scope тикета (добавлены OpenRGB, OpenRazer, официальный GitLab SignalRGB) — additive, обоснование в тикете.
Architecture notes: подтверждает решения по `quirk`-схеме и приоритету hardware-профиля (совпадение с моделью libratbag делает будущее портирование дешёвым, если модель не сломать).
Verification evidence: ручная проверка полноты против §2 плана — выполнена.
Commit: не создавался (git-репозитория нет до TICKET-07).
Follow-up tickets: нужен артефакт third-party notices к моменту первой фактической инкорпорации MIT-кода (фаза 6 / TICKET-19) — отдельный тикет пока не создан осознанно.

### TICKET-01 — Конспект протокола ROYUAN/sharkfin

Status: DONE_WITH_DEVIATIONS (2026-08-17)
Tracker reference: локальный (`issues/01-study-sharkfin-protocol.md`)
Dependencies: нет
Acceptance criteria: все три выполнены
Implementation summary: `docs/prior-art/royuan.md` — транспорт (usage page `0xFFFF`/usage `2`/report ID `0`/64 байта), семейства (yc500, gen2, под-линии yc3121/yc3123/ry5088) и правило «семейство только из реестра, не из PID», две схемы контрольных сумм, полная опкод-таблица, перечень отвечающих/не отвечающих опкодов на плате X86, HE-слой, деструктивные опкоды, измеренные тайминги, форматы данных. Источник запинен коммитом `d657f19` (v0.3.5).
Deviations: (1) исходники sharkfin построчно не читались — только `docs/PROTOCOL.md`, сознательно, чтобы не заимствовать выражение из GPL-кода; таблица 949 плат перенесена в scope TICKET-05. (2) Внесено additive-правило в `spec.md` § Domain rules. Полные записи — в тикете.
Architecture notes: `pregistry` получает конкретные сигналы fingerprint (identify `0x8F`, revision `0x80`); `psafety` получает реальные значения rate limiter для ROYUAN; подтверждено, что `opcode_acl` обязан быть per-family (задокументированные коллизии `0x06`/`0x09`/`0x11` и factory reset `0x01`↔`0x02`); включение аналогового стрима — операция записи, т.е. Analog Monitor не исключение из FR2.
Verification evidence: ручная — документ самодостаточен для старта Safe Probe в TICKET-09 (см. Review findings тикета).
Commit: не создавался (git-репозитория нет до TICKET-07).
Follow-up tickets: нет новых; уточнения учтены в TICKET-05/09/10/15.

### TICKET-08 — HID-инвентарь AULA Hero 84 HE

Status: DONE_WITH_DEVIATIONS (2026-08-17)
Tracker reference: локальный (`issues/08-windows-hid-inventory-aula.md`)
Dependencies: TICKET-07 (done)
Acceptance criteria: все выполнены; вопрос об аналоговом TLC закрыт ответом «не удалось определить без I/O», что тикетом предусмотрено
Implementation summary: `ptransport::inventory` + dev-инструмент `examples/inventory.rs`; артефакты `docs/hardware/aula-hero-84-he.{json,md}` схемы `peripheral.hid-inventory/1`, формат описан в `docs/hardware/README.md`. Снято: VID `0x372E`, PID `0x103E`, `BY Tech` / `HERO 84 HE`; 7 коллекций на 3 интерфейсах, все открылись без admin; **два** vendor-TLC — `0xFF00:0x0001` (feature 64 B) и `0xFF60:0x0061` (in/out 64 B).
Deviations: четыре, см. тикет. Существенная одна — содержимое vendor-каналов не определялось, поскольку это требует обмена с устройством.
Architecture notes: FR10 требует уточнения формулировки — одно устройство даёт набор handle'ов, а не один (отнесено в чекпоинт перед фазой 3). FR7 держится: `hidapi` покрыл все нужные данные, escape hatch не понадобился.
Verification evidence: fmt/clippy/build/test/deny/DAG — зелёные; прогон на железе; полнота сверена через `Get-PnpDevice` (3 интерфейса → 7 коллекций); отсутствие серийника в артефактах проверено.
Commit: `503070e`
Follow-up tickets: нет новых. Уточнения адресованы в TICKET-05/10 (гипотеза `0xFF60:0x0061`), TICKET-10 (продуктовый парсер дескрипторов с фаззингом), TICKET-12 (первый I/O → проверка находок 2 и 3).

### TICKET-11 — `psafety`: ACL, `SafeCommandId`, rate limiter, journal

Status: DONE_WITH_DEVIATIONS (2026-08-18)
Tracker reference: локальный (`issues/11-psafety-acl-skeleton.md`)
Dependencies: TICKET-07 (done)
Acceptance criteria: все четыре выполнены
Implementation summary: `data/protocols/*.toml` (ACL по семейству, схема `peripheral.opcode-acl/1`) → `build.rs` → закрытый `SafeCommandId`. Крейт: `class`, `command`, `rate`, `journal`, `gate`, плюс генератор `codegen.rs`, включаемый и в build script, и в тесты. Сгенерировано 16 команд, **все read/probe**; write-команд ноль; у `aula-hero84-he` — ноль команд вообще.
Deviations: пять, см. тикет. Существенных две: `trybuild` не использован (снапшоты stderr протухают на канале `stable`, а CI ещё ни разу не прогонялся — риск ложных падений в наборе, который должен вызывать доверие); ACL в TOML, а не YAML (парсер — часть границы безопасности, нужен поддерживаемый парсер с `deny_unknown_fields`).
Architecture notes: гейт **владеет** sink'ом, а не одалживает его, поэтому у engine нет объекта для обхода; `AuthorizedCommand` невозможно ни сконструировать снаружи, ни переиграть; `SafeCommandId::opcode()` — `pub(crate)`, байт читается ровно в одном месте. Гейт оперирует `DeviceId`, а не `SessionHandle`: сессиями владеет sink, второго места владения устройством не появилось (FR10). Kill-switch реагирует только на типизированный `EndpointStalled` — текст ошибки backend'а не разбирается (находка TICKET-08).
Verification evidence: fmt/clippy `-D warnings`/build/test/deny/DAG — зелёные, 65 тестов в workspace (64 из них — `psafety`). Отдельно: сборка **падает** при попытке внести деструктивный байт как `safe_write`; мутационная проверка тестов гейта (снятие family-check роняет 1 тест, снятие карантина — 3).
Commit: `34e786c`
Follow-up tickets: нет новых. Открытые концы адресованы в TICKET-10 (confidence в binding'е гейта), TICKET-12/15 (write-API транспорта обязан принимать только продукт гейта; `Verification::Pending` → `Confirmed`).

## Decisions

Полный протокол интервью — `EXECUTION_LOG.md`. Сводка:

1. **Лицензия:** proprietary/commercial; GPL — только prior art. (2026-08-17, пользователь)
2. **Sharkfin:** независимая разработка + контакт с автором ради обмена находками, не кода/лицензии. (2026-08-17, пользователь)
3. **Позиционирование:** HE-first в маркетинге, universal в архитектуре. (2026-08-17, пользователь, совпадает с дефолтом исходного плана)
4. **Референсное железо:** AULA Hero 84 HE (в наличии, primary) + ROYUAN-плата (к покупке, secondary). Оба трека — с самого начала Phase 1, не последовательно. (2026-08-17, пользователь) — **отменено решением 9 того же дня, см. ниже.**
5. **Архитектурные правки транспорта/IPC/крейтов** (2026-08-17, code review плана): `hidapi` как основная транспортная абстракция вместо трёх независимых платформенных реализаций (Windows escape hatch на прямой Win32 — только при нехватке `hidapi`); физический HID-handle владеется исключительно `DeviceSession` в выделенном worker-потоке, не «просто opaque handle»; IPC `pcore`↔UI — три отдельных механизма Tauri (commands/events/channels), Channels обязателен для `he.analog_stream`; крейты образуют явный однонаправленный DAG (`architecture/INITIAL_REVIEW.md` §9), не просто «не знают друг о друге». Применено к `spec.md` (FR7, FR10, FR11) и тикетам 07/08/09/11/13/14.
6. **Визуальная палитра + System Tray** (2026-08-17, предложение пользователя): добавлена черновая палитра (White/Grey/Gold/Black, `spec.md` Приложение C, с флагом на нейминг токена «Real Madrid Gold» — не блокер, но требует ревью перед публичным брендингом). Добавлена фича System Tray с динамической battery-иконкой (`spec.md` FR12) и генерализована capability `mouse.battery` → `dev.power.battery`/`dev.power.charging` + новая `dev.connection` — заряд относится к любому wireless-устройству, не только к мыши. Новый эпик TICKET-20.
7. **Доменное правило «отсутствие ответа — не доказательство отсутствия возможности»** (2026-08-17, итог TICKET-01; решения пользователя не требовало — additive, ничего не ослабляет). Основание: sharkfin с маркером `[HW]` фиксирует, что HE-опкоды не отвечают на Attack Shark X86, тогда как на X68 PRO HE (тот же VID `0x3151`) опкод `0x1B` рабочий и включает аналоговый стрим. Внесено в `spec.md` § Domain rules. Следствие: `Unsupported` требует того же уровня доказательства, что `Verified(hw)`; результат probe пишется как факт о плате+прошивке, не о семействе.
8. **Третий permissive-источник кода** (2026-08-17, итог TICKET-03): помимо libratbag (MIT, мыши) permissive также `ratbag-emu` (MIT → `tools/emu`) и `he-analog-gamepad` (MIT → аналоговый стрим для Sonix `0x3151`/`0x5030`). Самую рискованную фичу v1 можно портировать легально, а не только конспектировать. Породило известный пробел: нужен артефакт third-party notices (требование MIT-атрибуции), тикета пока нет.
9. **Референсное железо: ROYUAN-платы не будет; фаза 2 перестроена вокруг AULA + EPOMAKER** (2026-08-17, пользователь). Покупка отменена, TICKET-06 полностью заменён на идентификацию имеющейся EPOMAKER, TICKET-09 — на её HID-инвентарь тем же `ptransport` плюс сравнительный отчёт двух устройств. Философское следствие, зафиксированное явно: EPOMAKER **не объявляется** вторым protocol family до исследования; присвоение семейства делается по evidence, как того и требует спецификация. Фаза 5 переименована с «ROYUAN-engine» на «Second protocol family» — какое семейство станет вторым, решают данные фазы 2. Ни одно acceptance criterion не ослаблено: требование ко второму устройству осталось прежним (полный read-only инвентарь тем же транспортом), к нему добавлен сравнительный отчёт.
10. **`ptransport` проверяется на generic-ность вторым устройством, а не вторым семейством** (2026-08-17, следствие решения 9). Инвентарь EPOMAKER снимается тем же `ptransport::enumerate()`, что и AULA; отдельная ветка кода «под EPOMAKER» запрещена — если она понадобилась, это находка о транспорте, которую исправляют в транспорте. **Решение 11 усилило это требование**, а не отменило.
11. **EPOMAKER переведена из reference-hardware track в remote external-validation track** (2026-08-17, пользователь). Устройство существует и доступно для будущего тестирования, но физически находится у другого человека (отца пользователя). Установка developer-tooling (Rust, cargo, Build Tools, диагностические CLI, USBPcap/Wireshark, dev-сборки, ручные HID-команды) на чужой компьютер противоречит цели продукта и не даёт ценного product-level результата. Устройство будет исследовано **через обычную пользовательскую сборку Peripheral** с безопасным read-only inventory export. Следствия: TICKET-06 и TICKET-09 выведены из Phase 2 в состояние `DEFERRED_REMOTE_VALIDATION` с capability-гейтом; Phase 2 сведена к уже выполненному TICKET-08; TICKET-05 вынесен в параллельный research track; основной code path — 11 → 10 → 12 → 13 → 14 → 15 → 16; блокировка TICKET-17 на TICKET-09 снята. **Это усиливает план, а не ослабляет:** EPOMAKER становится более ценным тестом — проверкой того, способен ли продукт, разработанный на AULA, безопасно встретить неизвестное устройство на чужом ПК и самостоятельно собрать всё необходимое для его дальнейшего анализа, руками человека без dev-toolchain. Одновременно это репетиция будущего community-submission workflow. Риск «архитектура пока проверена только на одном protocol family» при этом **остаётся открытым и обязан быть виден в architecture review** — молча считать его закрытым нельзя.

## Global deviations

- Skill `ticket-autopilot` ссылается на дочерние скиллы (`grill-with-docs`, `to-spec`, `to-tickets`, `implement`, `handoff`, `wayfinder`, `improve-codebase-architecture`, `setup-matt-pocock-skills`), не установленные в этой сессии. Интервью проведено напрямую через `AskUserQuestion` вместо `/grill-with-docs`; `spec.md`/тикеты написаны вручную по шаблонам скилла вместо вызова `/to-spec`/`/to-tickets`. Функциональная цель фаз соблюдена, инструмент — заменён. См. `EXECUTION_LOG.md` для полной записи.
- TICKET-04 выполнен вне последовательности Phase 8 (см. его собственный Deviations) — обоснование там же.

## Known risks

Перенесены и приоритизированы из §15 плана применительно к текущему состоянию (ничего не реализовано):

| Риск | Относится к | Статус на сейчас |
|---|---|---|
| Окирпичивание чужой платы записью | TICKET-15+ | **Существенно снижен 2026-08-18 (TICKET-11).** Гейт существует и работает: деструктивные и неклассифицированные опкоды не имеют представления в программе, write-команд в реестре ноль, у нашей платы команд нет вообще. Остаточная часть: `ptransport` пока не имеет write-API, и когда он появится (TICKET-12/15), он обязан принимать только продукт гейта — до тех пор последний участок пути держится на соглашении, а не на типах. |
| Коллидирующие опкоды между суб-семействами | TICKET-12, TICKET-17 | **Механизирован 2026-08-18 (TICKET-11).** ACL keyed by family, и гейт отклоняет команду, чьё семейство не совпадает с семейством устройства (`Refusal::WrongFamily`). Пара `royuan-gen2`/`royuan-yc500` внесена в реестр именно как живой пример коллизии: `0x06`, `0x09`, `0x11` и factory reset означают там разное. Остаётся правило `confidence >= Verified` — его носитель `pregistry` (TICKET-10), в гейте пока есть только семейство без confidence. |
| «Успешный» ответ на неподдержанную команду | TICKET-12+ | Учтено — `verify()` обязателен в `ProtocolEngine` (FR1), анти-фикция-фильтр обязателен в Learning Mode (FR5). |
| Windows TLC-блокировка закрывает нужную коллекцию | TICKET-08/09 | **Снят для AULA** (2026-08-17): все 7 коллекций, включая оба vendor-TLC, открылись без прав администратора. Остаётся открытым для EPOMAKER — и теперь проверяется удалённо, на чужом ПК, где повысить права сложнее (TICKET-09). |
| «Открылось» ≠ «можно обмениваться» | TICKET-12 | Новый, 2026-08-17. На Windows backend при отказе в read/write открывает устройство с нулевыми правами; различить можно только отправив репорт. Если TICKET-12 прочитает инвентарь как «доступ есть», он будет отлаживать не ту проблему. |
| Классификация ошибок backend'а по тексту не работает | TICKET-12/15 | Изменён 2026-08-18. `hidapi` отдаёт сообщение без кода, на Windows — локализованное ОС; определение stall подстрокой на локализованной системе молча не срабатывает. TICKET-11 **не** стал это чинить, а зафиксировал границу: kill-switch реагирует только на типизированный `EndpointStalled`, текст `Backend(String)` не разбирается нигде, и это покрыто тестом с русскоязычным сообщением. Следствие: ответственность переехала в транспорт — если он не сумеет типизировать stall, kill-switch не сработает. Решение о platform-native классификации принимается при первом настоящем I/O (TICKET-12/15). |
| **Архитектура валидирована на одном устройстве и одном protocol family** | TICKET-10/12/17, architecture review риск №3 | **Усилен 2026-08-17** (вторая мутация дня). Второй платы на столе разработчика нет вообще: ROYUAN не покупается, EPOMAKER у стороннего владельца и доступна только удалённо и только поздно (capability-гейт). До первого remote-артефакта единственное «второе устройство» в тестах — эмулятор, собранный из записей самой AULA, а значит не способный опровергнуть AULA-специфичные предположения. `ProtocolEngine`/`CapValue` рискуют дойти до фазы 4 провалидированными на одном семействе. Митигация — частичная: синтетические fingerprint-тесты (TICKET-10), эмулятор (TICKET-14), запрет считать трейт финальным до второго семейства. **Риск остаётся открытым; молча считать его закрытым переносом EPOMAKER нельзя.** |
| Remote-валидация может не состояться или дать неполные данные | TICKET-09/06 | Новый, 2026-08-17. Данные зависят от постороннего человека, его готовности запустить сборку и от того, окажется ли UX достаточно понятным. Митигация: путь ограничен четырьмя шагами (`скачать → запустить → подключить → Export`), гейт входа — «сборка, которую не стыдно дать другому человеку». Неудача сценария — сама по себе валидный результат о продукте, а не о железе. |
| Выгорание на масштабе (тысячи моделей вручную) | TICKET-18 | Смягчается ingestion pipeline + community submissions с Phase 3, архитектурно заложено с самого начала (§7 плана). |
| Юридическое письмо от вендора | TICKET-18 | Смягчается §14 плана — соблюдено в scope TICKET-18. |

## Deferred work

См. `spec.md` § Open questions → DEFERRED: итоговый нейминг продукта, firmware update/recovery, ViGEm-вывод, гарнитуры/DAC, мобильное/веб-хранилище профилей.

## Final acceptance checklist

Неприменимо на этом этапе — ни один тикет реализации не начат. Чеклист активируется при переходе в FINAL_REVIEW после завершения всех Phase 1–N тикетов.

- [ ] Every required ticket completed — **не начато**
- [ ] Full test suite or agreed equivalent run — **не начато**
- [ ] Specification reviewed requirement by requirement — n/a (спецификация только что создана)
- [ ] No unresolved blocking review findings — n/a
- [ ] Migration and compatibility behavior verified — n/a (нет миграций, greenfield)
- [ ] User-visible behavior verified — **не начато**
- [ ] Deferred work explicitly recorded — **готово**, см. `spec.md`
- [ ] Final architecture checkpoint completed — n/a на этом этапе
