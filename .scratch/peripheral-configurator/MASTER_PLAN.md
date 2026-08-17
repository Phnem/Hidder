# Peripheral (`peripheral-core`) — Master Plan

## Workflow

Current workflow state: READY_FOR_IMPLEMENTATION
Current ticket: None
Last completed ticket: TICKET-08 (HID-инвентарь AULA Hero 84 HE)
Next eligible ticket: TICKET-06 (идентификация EPOMAKER) — по порядку фазы 2. Параллельно доступны TICKET-05 и TICKET-11.
Last updated: 2026-08-17

Реализация начата 2026-08-17 (пользователь: «раздели план на фазы и начинай фазу 1»). Phase 1 по разбивке ниже — документационные тикеты (03/01) + инфраструктурный скелет (07).

## Execution phases

Разбивка 20 тикетов на исполнительные фазы. Фазы — про порядок работы в этой сессии/сессиях; нумерация НЕ совпадает с «Phase 0..6» исходного плана (там фазы продукта). Соответствие указано в колонке «План».

| Фаза | Содержание | Тикеты | План | Входной гейт |
|---|---|---|---|---|
| **1. Знание + фундамент** | Prior-art карта, конспект ROYUAN-протокола, изучение методов sharkfin, cargo workspace + Tauri-скелет + CI + `git init` | 03, 01, 21, 07 | Phase 0 + начало Phase 1 | нет (07 — Rust toolchain на машине) |
| **2. Реальность железа** | Карта платформ, идентификация EPOMAKER, HID-инвентарь AULA и EPOMAKER одним транспортом, сравнение | 05, 06, 08, 09 | Phase 0/1 | 07 (выполнен); железо в наличии |
| **3. Read-only вертикальный срез** | Fingerprinting, `psafety`-скелет, первый protocol engine (AULA read-only), Tauri UI, эмулятор+CI-тест | 10, 11, 12, 13, 14 | Phase 1 DoD (AC2) | 07, 08 |
| **4. Запись и аналог** | Verified write + Analog Monitor, Learning Mode | 15, 16 | Phase 2/3 | фаза 3 целиком |
| **5. Второе семейство + релиз** | Second protocol family (какое именно — решают данные фазы 2, не план), профили, ingestion pipeline, tray-оболочка | 17, 18, 20 (shell-трек) | Phase 3/4 | 09, 15 |
| **6. Мыши и wireless** | Мышиный слой, реальные battery-данные в трее | 19, 20 (data-трек) | Phase 6 | 17 |

### Phase 2 — состав и порядок (перестроена 2026-08-17)

Фаза перестроена по решению пользователя: **ROYUAN-платы не будет**. Фаза больше не про «найти второе семейство любой ценой», а про два реально имеющихся устройства — **AULA Hero 84 HE** и полноразмерную **EPOMAKER**.

```text
TICKET-05  карта платформ + карточки наших устройств   (можно параллельно)
TICKET-06  точная идентификация EPOMAKER
                    ↓
        ┌───────────────────┐
        ▼                   ▼
  TICKET-08            TICKET-09
  AULA inventory       EPOMAKER inventory   ← тот же ptransport
        └─────────┬─────────┘
                  ▼
          сравнение двух inventory
                  ▼
        архитектурные выводы → фаза 3
```

Рекомендованная последовательность работ: **08 → 06 → 09 → сравнение**, с 05 параллельно. TICKET-11 (`psafety` skeleton) железа не требует и идёт параллельно всей фазе.

**EPOMAKER заранее не объявляется ни ROYUAN, ни вообще вторым protocol family.** Это должно доказать железо. Четыре возможных исхода, и ни один не делает устройство бесполезным:

| Исход | Что это даёт |
|---|---|
| A. Другой HE-протокол | идеально — становится second-family reference |
| B. Тот же или OEM-родственный протокол | тоже ценно — проверка fingerprinting на двух моделях одного семейства |
| C. Обычная механика, не HE | остаётся отличным generic HID / transport test device, но вторую HE-family не заменяет |
| D. Vendor config-интерфейс отсутствует | полезный негативный тест: приложение обязано сказать «Config interface unavailable», а не писать наугад |

**DoD фазы 2:** на Windows через один и тот же `ptransport` получен полный read-only HID-инвентарь AULA Hero 84 HE и EPOMAKER; для каждого устройства сохранены дескрипторы и fingerprint-сигналы; установлено наличие или отсутствие доступного vendor config TLC; EPOMAKER классифицирована настолько, насколько позволяют наблюдаемые данные, **без предположений по бренду**; сформирован сравнительный отчёт двух устройств.

Следствие, важное для фазы 5: **решение о втором protocol engine принимается по данным фазы 2, а не заранее планом.** Отсюда переименование фазы 5 с «ROYUAN-engine» на «Second protocol family».

Правила перехода: фаза считается закрытой, когда все её тикеты имеют статус DONE/DONE_WITH_DEVIATIONS либо явно перенесены в следующую фазу с записью в `EXECUTION_LOG.md`. Тикет 02 (письмо автору sharkfin) — единственный, чьё завершение требует действия пользователя (отправка сообщения), поэтому в фазе 1 выполняется только черновик. Архитектурный чекпоинт — между фазой 3 и 4, и повторно перед 5 (нужен для нерешённого вопроса «tray-процесс vs process watcher», `spec.md` SAFE_DEFAULT).

### Phase 1 status

| Тикет | Статус | Примечание |
|---|---|---|
| TICKET-03 | DONE | `docs/prior-art/inventory.md` |
| TICKET-01 | DONE_WITH_DEVIATIONS | `docs/prior-art/royuan.md`; исходники sharkfin не читались (только `PROTOCOL.md`) — обоснование в тикете |
| TICKET-21 | DONE | `docs/prior-art/sharkfin-methods.md`; заменил черновик TICKET-02 по решению пользователя |
| TICKET-02 | READY, отложен пользователем | Отправка сообщения третьему лицу требует явного разрешения; предмет обмена станет содержательнее после TICKET-08 |
| TICKET-07 | DONE_WITH_DEVIATIONS | Коммит `f770750`. Единственное отклонение: CI написан, но не исполнялся — нет remote-репозитория. |

**Фаза 1 закрыта** (2026-08-17). Не выполнен только TICKET-02, отложенный самим пользователем; он не блокирует ничего в фазе 2.

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
- Референсное железо: **AULA Hero 84 HE** (в наличии, primary) + полноразмерная **EPOMAKER** (в наличии, secondary). Обновлено 2026-08-17: покупка ROYUAN-платы отменена решением пользователя. **Принадлежность EPOMAKER к какому-либо protocol family не предполагается заранее** — её устанавливает фаза 2 по данным.

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

Hardware-in-the-loop чеклист появится содержательно с TICKET-08 (первое реальное железо в цикле).

## Ticket overview

| ID | Title | Status | Blocked by | Commit | Review |
|---|---|---|---|---|---|
| TICKET-01 | Изучить протокол sharkfin/ROYUAN | **DONE_WITH_DEVIATIONS** | — | — | самопроверка, блокеров нет |
| TICKET-02 | Связаться с автором sharkfin | READY (черновик; отправка — за пользователем) | TICKET-01 (done) | — | — |
| TICKET-03 | Инвентаризация prior art | **DONE** | — | — | самопроверка, блокеров нет |
| TICKET-04 | Лицензия проекта (ADR-0001) | **DONE** | — | — | — |
| TICKET-05 | Карта платформ + карточки наших устройств | READY | TICKET-01 (done), TICKET-03 (done) | — | — |
| TICKET-06 | Идентификация референсной платы EPOMAKER | READY | — (устройство в наличии) | — | — |
| TICKET-07 | Cargo workspace + Tauri skeleton + CI | **DONE_WITH_DEVIATIONS** | TICKET-04 (done) | `f770750` | самопроверка, блокеров нет |
| TICKET-08 | Windows HID inventory — AULA | **DONE_WITH_DEVIATIONS** | TICKET-07 (done) | `TBD` | самопроверка, блокеров нет |
| TICKET-09 | Windows HID inventory — EPOMAKER + сравнение двух устройств | PENDING | TICKET-06, TICKET-08 | — | — |
| TICKET-10 | Multi-signal fingerprinting (`pregistry`) | PENDING | TICKET-08 (07 done) | — | — |
| TICKET-11 | `psafety` ACL + `SafeCommandId` skeleton | READY | TICKET-07 (done) | — | — |
| TICKET-12 | Первый protocol engine (AULA, read-only) | PENDING | TICKET-08, TICKET-10, TICKET-11 | — | — |
| TICKET-13 | Tauri UI skeleton (Devices/HE/Journal) | PENDING | TICKET-07, TICKET-12 | — | — |
| TICKET-14 | Device emulator + CI fingerprint-тест | PENDING | TICKET-08, TICKET-10 | — | — |
| TICKET-15 | EPIC: Verified write + Analog Monitor | PENDING | TICKET-07..14 | — | — |
| TICKET-16 | EPIC: Learning Mode | PENDING | TICKET-15 | — | — |
| TICKET-17 | EPIC: second protocol family + профили + релиз v0.1 | PENDING | TICKET-09, TICKET-15 | — | — |
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
Commit: TBD
Follow-up tickets: нет новых. Уточнения адресованы в TICKET-05/10 (гипотеза `0xFF60:0x0061`), TICKET-10 (продуктовый парсер дескрипторов с фаззингом), TICKET-12 (первый I/O → проверка находок 2 и 3).

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
10. **`ptransport` проверяется на generic-ность вторым устройством, а не вторым семейством** (2026-08-17, следствие решения 9). Инвентарь EPOMAKER снимается тем же `ptransport::enumerate()`, что и AULA; отдельная ветка кода «под EPOMAKER» запрещена — если она понадобилась, это находка о транспорте, которую исправляют в транспорте.

## Global deviations

- Skill `ticket-autopilot` ссылается на дочерние скиллы (`grill-with-docs`, `to-spec`, `to-tickets`, `implement`, `handoff`, `wayfinder`, `improve-codebase-architecture`, `setup-matt-pocock-skills`), не установленные в этой сессии. Интервью проведено напрямую через `AskUserQuestion` вместо `/grill-with-docs`; `spec.md`/тикеты написаны вручную по шаблонам скилла вместо вызова `/to-spec`/`/to-tickets`. Функциональная цель фаз соблюдена, инструмент — заменён. См. `EXECUTION_LOG.md` для полной записи.
- TICKET-04 выполнен вне последовательности Phase 8 (см. его собственный Deviations) — обоснование там же.

## Known risks

Перенесены и приоритизированы из §15 плана применительно к текущему состоянию (ничего не реализовано):

| Риск | Относится к | Статус на сейчас |
|---|---|---|
| Окирпичивание чужой платы записью | TICKET-15+ | Не актуален — записи ещё нет. Архитектурный gate (`SafeCommandId`) заложен в план TICKET-11. |
| Коллидирующие опкоды между суб-семействами | TICKET-12, TICKET-17 | Учтено доменным правилом в `spec.md` — write только при `confidence >= Verified` для конкретной family. |
| «Успешный» ответ на неподдержанную команду | TICKET-12+ | Учтено — `verify()` обязателен в `ProtocolEngine` (FR1), анти-фикция-фильтр обязателен в Learning Mode (FR5). |
| Windows TLC-блокировка закрывает нужную коллекцию | TICKET-08/09 | **Снят для AULA** (2026-08-17): все 7 коллекций, включая оба vendor-TLC, открылись без прав администратора. Остаётся открытым для EPOMAKER (TICKET-09). |
| «Открылось» ≠ «можно обмениваться» | TICKET-12 | Новый, 2026-08-17. На Windows backend при отказе в read/write открывает устройство с нулевыми правами; различить можно только отправив репорт. Если TICKET-12 прочитает инвентарь как «доступ есть», он будет отлаживать не ту проблему. |
| Классификация ошибок backend'а по тексту не работает | TICKET-11/12 | Новый, 2026-08-17. `hidapi` отдаёт сообщение без кода, на Windows — локализованное ОС. Определение stall подстрокой (как у prior art) на локализованной системе молча не срабатывает, то есть kill-switch FR3 не сработает. `EndpointStalled`/`AccessDenied` обязаны определяться кодом платформы — первый конкретный кандидат на Win32 escape hatch. |
| Второе устройство может не дать второго protocol family | TICKET-06/09 | Изменён 2026-08-17. Прежняя формулировка («не хватает железа, ждём доставки ROYUAN») больше не актуальна: покупки нет, оба устройства в наличии. Новый риск — EPOMAKER может оказаться механической, родственной AULA или без доступного config-интерфейса, и тогда второго HE-семейства в фазе 2 не появится. Митигация: все четыре исхода заранее признаны полезными (см. Phase 2), решение о втором engine принимается по данным в фазе 5, а не планируется заранее. Остаточный риск реален: `ProtocolEngine` может дойти до фазы 4 провалидированным на одном семействе — это ровно риск №3 architecture review («premature trait finalization»), и он остаётся открытым. |
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
