# Execution log — peripheral-configurator

## 2026-08-17 — Initial codebase discovery

### Relevant modules

None. `D:\AndroidStudioProjects\Vetro hud` contains only `.claude/` (skills/config). No source tree, no `Cargo.toml`, no `package.json`, no `AGENTS.md`/`CLAUDE.md`, no `CONTEXT.md`, no ADRs, no git repository (`git status` not applicable — directory is not a repo yet).

### Existing behavior

N/A — greenfield.

### Existing terminology

None established locally. Canonical source of terminology is the input planning document (`peripheral-configurator-plan (1).md`, v0.2) and the prior-art projects it references (sharkfin, libratbag, libhmk, minipad-firmware). Terminology to inherit: `actuation`, `Rapid Trigger` (down/up/continuous), `DKS`, `SOCD`/null-bind, `dead zone`, `TLC` (top-level collection), `protocol family`, `capability`, `origin/evidence marker`.

### Existing tests

None.

### Constraints discovered

- No repository, no CI, no build tooling yet — Phase 0/1 tickets must include repo initialization (git init, cargo workspace, CI config) as explicit enabling work, not assume it exists.
- Working directory name ("Vetro hud") is unrelated to this project; confirmed with user this is intentionally a fresh project living in this folder, not a rename of an existing app. (No explicit confirmation was requested — inferred from the directory being empty and the user directly proceeding with this plan. Flagged in Decisions as an assumption, not blocking.)
- `.claude/skills/ticket-autopilot/SKILL.md` exists as a project-scoped skill; several skills it references as "required" or "conditional" (`grill-with-docs`, `to-spec`, `to-tickets`, `implement`, `handoff`, `wayfinder`, `improve-codebase-architecture`, `setup-matt-pocock-skills`, `ask-matt`) are **not installed** in this session's skill set. Substituted: `grilling` + direct `AskUserQuestion` for the interview phase (in place of `grill-with-docs`); manual authoring against the skill's own templates for `to-spec`/`to-tickets` output shape; `handoff`/`implement` are deferred entirely because this run stops before Phase 8 (implementation) per explicit user instruction.

### Questions answerable from code

None (no code exists).

### Remaining material uncertainties

See `spec.md` § Open questions for the full BLOCKING/SAFE_DEFAULT/DEFERRED classification. The four questions the source document itself marked blocking (§17, items 1–4) were resolved via interview below.

## 2026-08-17 — User interview (INTERVIEW phase)

Conducted via a single batched `AskUserQuestion` covering the four blocking decisions the source plan (§17) flagged as needing resolution before code. Answers, verbatim intent:

1. **License** — Proprietary/commercial. The app will be sold as closed-source software. GPL projects (sharkfin, OpenRGB, OpenRazer) may be used only as prior art / source of protocol facts, with independent reimplementation of any code. MIT/Apache-2.0/BSD dependencies are acceptable in the shipped binary. GPL/AGPL code must never be linked into or copied into the product.
2. **Sharkfin relationship** — Build independently as a proprietary product. Still contact the sharkfin author for technical discussion and possible knowledge-sharing (protocol research, hardware findings, compatibility notes) — cooperation on *information*, not on *code or licensing*.
3. **Positioning** — HE-first in marketing and specification framing; universal (`peripheral-core`, not `he-core`) in architecture, matching the source document's own recommended default.
4. **Reference hardware** — User already owns an **AULA Hero 84 HE** (non-ROYUAN) — this becomes the primary Phase 0/1 reference board. A ROYUAN-family HE keyboard will additionally be purchased as the second protocol family. Phase 0/1 tickets target both boards from the start (AULA first since it's on hand now; ROYUAN track is blocked on purchase/delivery).

These decisions are treated as final for this planning pass and are carried into `spec.md`, the license ADR, and the ticket set. Per the skill's "preserve user decisions" rule, they will not be re-asked absent new contradicting evidence.

## 2026-08-17 — Post-planning corrections and scope addition (plan mutation)

Two follow-up rounds of user feedback, applied directly to the planning artifacts (no code affected — repository still has zero implementation):

**Round 1 — architecture corrections (4 items).** User reviewed the transport/IPC/crate-boundary language in `architecture/INITIAL_REVIEW.md` and `spec.md` and supplied more precise formulations: `hidapi` as the primary cross-platform transport abstraction (not three independent platform implementations, with native Win32/IOKit only as an in-crate escape hatch); a `DeviceSession`/dedicated-worker ownership model for the physical HID handle instead of a bare "opaque handle" (reasoning: `hidapi::HidDevice` is `Send` but not `Sync`, HID reads can block); a three-mechanism Tauri IPC split (commands/events/Channels, with Channels mandatory for `he.analog_stream` — Tauri's own docs note async event listeners can reorder); and an explicit one-way crate dependency DAG rather than "crates don't know about each other." Applied to `spec.md` (FR7, FR10, FR11) and `architecture/INITIAL_REVIEW.md` (§4, §6, new §9, expanded Risks), plus tickets 07/08/09/11/13/14.

**Round 2 — scope addition: visual palette + System Tray.** User supplied a draft color palette (White `#FFFFFF`, Grey `#F2F2F2`, "Real Madrid Gold" `#FEBE10`, Night Black `#1A1A1A`) and a detailed System Tray + battery-indicator proposal (Tauri 2 `TrayIcon`, in-memory icon rendering by charge percent, quick panel of connected devices, lowest-battery-wins icon selection, low-battery warnings). Applied as:
- `spec.md` Приложение C (palette, marked SAFE_DEFAULT / draft, with an explicit non-blocking flag: the token name "Real Madrid Gold" references a football club brand — fine as an internal design-token name, but should not appear in public marketing/product naming without trademark review, and should likely be renamed before any published style guide).
- `spec.md` capability table: `mouse.battery` generalized to `dev.power.battery`/`dev.power.charging` + new `dev.connection` — battery/connection type is a property of any wireless peripheral, not specifically mice.
- `spec.md` FR12 (tray/icon rendering requirements), new domain rule (battery has three possible origins — standard HID usage, vendor opcode, receiver status — capability layer hides which one), new User-visible-behavior bullet (System Tray), new Test seam (battery-selection logic).
- New epic `TICKET-20` in `issues/20-epic-system-tray-battery.md` — explicitly split into a tray-shell track (unblocked once TICKET-13 lands) and a real-battery-data track (blocked until a wireless-capable protocol engine exists — neither Phase 0/1 reference board is wireless, per the existing domain rule that ROYUAN/AULA-class boards have no 2.4GHz/BT config channel).
- `MASTER_PLAN.md` ticket overview + Decisions updated; flagged for reconciliation, not silently overridden: the plan's existing SAFE_DEFAULT about a "thin opt-in tray process, separate from the GUI" for process-preset watching (source plan §17.9) may need revisiting now that the main app has its own persistent Tauri tray icon — left as an open reconciliation point for the architecture checkpoint before TICKET-17/20, not resolved here.

No tickets from Phase 0/1 were reordered or had acceptance criteria weakened by either round; both are additive per the skill's "Adding a ticket" mutation rule (discovery source: user code-review of the plan; scope impact: capability table generalization + one new epic, no changes to already-READY/DONE ticket acceptance criteria).

## 2026-08-17 — Execution phases defined; implementation started

Пользователь: «найди мастер план и др материалы, раздели для себя план на фазы и начинай фазу 1». 20 тикетов разбиты на 6 исполнительных фаз (таблица — `MASTER_PLAN.md` § Execution phases). Нумерация фаз исполнения намеренно не совпадает с «Phase 0..6» исходного плана (там фазы продукта) — соответствие указано в таблице. Состояние workflow переведено из READY_FOR_IMPLEMENTATION в IMPLEMENTING_TICKET.

Фаза 1 = TICKET-03 + TICKET-01 (документационные, ничего не требуют кроме доступа к сети) + TICKET-07 (инфраструктурный скелет) + черновик TICKET-02.

**Обнаруженное ограничение среды:** на машине отсутствует Rust-тулчейн (`cargo`/`rustc` нет ни в PATH bash, ни в PATH PowerShell, каталога `~/.cargo/bin` нет). Присутствуют Node.js 24.15.0, npm 11.12.1, git 2.55.0. Это делает TICKET-07 BLOCKED до решения пользователя об установке rustup + MSVC build tools — установка тулчейна меняет машину пользователя и не входит в scope тикета, поэтому не выполняется без явного разрешения. Порядок фазы 1 выбран так, чтобы этот блокер не задерживал остальное: сначала документационные тикеты, гейт — после них.

## 2026-08-17 — TICKET-03

### Outcome

DONE

### Work completed

`docs/prior-art/inventory.md`: 15 проектов (sharkfin, he-analog-gamepad, libratbag, ratbag-emu, libhmk, hmkconf, minipad-firmware, Piper, VIA, Vial, QMK, sinowisp, OpenRGB, OpenRazer, SignalRGB-плагины) с проверенной по первоисточнику лицензией, режимом использования `code`/`facts`/`docs` со ссылкой на ADR-0001, разделом о планируемой инкорпорации permissive-кода и его влиянии на дизайн `MouseProtocol`/`pprofile`/`quirk`, отдельным разделом по he-analog-gamepad (требование acceptance criteria) и явным перечнем непроверенного.

### Decisions made

- Введено правило default-deny для лицензии, которую не удалось подтвердить: режим `facts`, никогда `code` (по аналогии с `unknown = запрещено` в `opcode_acl`). Применено к официальному репозиторию SignalRGB-плагинов, у которого лицензия на странице проекта не отображается.
- `sinowisp` переведён в режим `facts` **политикой, а не лицензией**: лицензия MIT это позволяет, но вся предметная область (ISP-бутлоадер, запись flash) запрещена глобальным ограничением «никакой прошивки в v1».

### Deviations

Таблица шире scope тикета: добавлены OpenRGB, OpenRazer (фигурируют в лицензионной политике `MASTER_PLAN.md`, но не в списке тикета) и официальный GitLab-репозиторий SignalRGB вместо только GitHub-сборников. Additive, ничего не ослаблено.

### Root causes discovered

N/A (не диагностический тикет).

### Verification

Ручная проверка полноты против §2 плана + сверка каждой acceptance criterion. Лицензии проверялись через страницы репозиториев (WebFetch/WebSearch) 2026-08-17; всё неподтверждённое помечено в документе как неподтверждённое.

### Review result

Самопроверка, 5 пунктов PASS, блокирующих findings нет. (Полноценный `/code-review` неприменим: документационный тикет, кода нет, git-diff'а нет.)

### Architecture observations

Модель libratbag («профиль — сущность на устройстве», отдельный слой quirks) совпадает с уже заложенной у нас (`dev.profiles`, приоритет hardware-профиля в FR6, таблица `quirk` в Приложении B). Совпадение делает будущее портирование дешёвым — и означает, что менять эти решения без веской причины дорого.

### New risks

MIT-атрибуция: без артефакта third-party notices инкорпорация permissive-кода станет нарушением условий лицензий. Риск не активен до фактического портирования (фаза 6), зафиксирован в документе и в Decisions мастер-плана.

### Follow-up work

Third-party notices артефакт — создать тикет при первой фактической инкорпорации permissive-кода, не раньше.

### Next eligible ticket

TICKET-01.

## 2026-08-17 — TICKET-01

### Outcome

DONE_WITH_DEVIATIONS

### Work completed

`docs/prior-art/royuan.md` — конспект протокола ROYUAN по `docs/PROTOCOL.md` sharkfin, запиненному на коммит `d657f199a82a46c45ab5d1327d88e843689ce6a9` («version 0.3.5», 2026-08-17 02:04:04 UTC): транспорт, семейства и правило их определения, две схемы контрольных сумм, опкод-таблицы (общие/yc500/gen2), перечень отвечающих и не отвечающих опкодов на плате X86, HE-слой, деструктивные опкоды, тайминги/rate limiting, форматы данных (слоты раскладки, макросы, LED-параметры). Аналоговый стрим X68 PRO HE добавлен из `he-analog-gamepad`.

### Decisions made

- Новое доменное правило в `spec.md`: **отсутствие ответа — не доказательство отсутствия возможности**. `Unsupported` требует того же уровня доказательства, что `Verified(hw)`; результат probe фиксируется как факт о плате+прошивке, не о семействе.
- Исходники sharkfin сознательно не читались (только `PROTOCOL.md`) — режим `facts` из ADR-0001 при нулевом приросте фактов от чтения GPL-кода.
- Таблица VID:PID по 949 платам отнесена к TICKET-05 и должна браться как **данные** с отдельной проверкой лицензии на данные.

### Deviations

Две, обе зафиксированы полностью в `issues/01-*.md`: (1) сужение scope чтения до `PROTOCOL.md`; (2) правка `spec.md` § Domain rules — артефакта уровнем выше тикета, additive.

### Root causes discovered

Наблюдение, объясняющее исходную постановку задачи проекта: HE-слой у ROYUAN «не отвечен прошивкой» не потому, что опкодов нет, а потому что на разных платах одного семейства блок HE-опкодов реализован неодинаково. Ниша существует именно из-за этой неоднородности — и именно она делает наш многосигнальный fingerprint с per-board evidence обязательным, а не приятным дополнением.

### Verification

Ручная, по verification plan тикета: документ проверен на самодостаточность для старта Safe Probe в TICKET-09 (есть транспорт, список безопасных read-опкодов, деструктивный список, тайминги, явный перечень вопросов к эмпирической проверке). 6 пунктов самопроверки PASS.

### Review result

Блокирующих findings нет.

### Architecture observations

Три конкретных входа в будущие тикеты: `pregistry` — сигналы fingerprint `0x8F` (identify, `u32` LE) и `0x80` (revision, `(байт2<<8)|байт1`); `psafety` — измеренные значения rate limiter для ROYUAN (~12 мс между репортами, ≤2 загрузки/10 с, 100 мс между страницами, 2 с settle) и подтверждение, что `opcode_acl` обязан быть per-family; `pproto` — вариант контрольной суммы выбирается по **опкоду**, а не по устройству.

### New risks

Включение аналогового стрима — операция **записи** (HID Feature Report `[0x1B, 0x01, …]`). Значит «read-only демо-фича» Analog Monitor не может быть исключением из FR2/`SafeCommandId`. Если это не учесть при декомпозиции TICKET-15, появится соблазн сделать для стрима обход `psafety` — то есть ровно тот BLOCKING-класс findings, который перечислен в рисках architecture review.

### Follow-up work

Новых тикетов нет. Уточнения адресованы в TICKET-05 (таблица плат), TICKET-08/09 (вопрос: один ли vendor-TLC у конфигурационного канала и аналогового стрима), TICKET-10 (сигналы fingerprint), TICKET-15 (стрим проходит через `psafety`).

### Next eligible ticket

TICKET-07 — **BLOCKED**: нет Rust-тулчейна, требуется решение пользователя. Незаблокированные альтернативы в очереди: TICKET-02 (черновик письма готовится агентом, отправка — за пользователем), TICKET-05 (OEM-карта — стал READY, относится к фазе 2).

## 2026-08-17 — Решения пользователя по блокеру и по TICKET-02

Задано два вопроса. Ответы:

1. **Rust-тулчейн** — установить rustup + MSVC на этой машине. Выполнено: rustup 1.29.0, `stable-x86_64-pc-windows-msvc` 1.97.1, VS 2022 Build Tools 17.14.37 (workload VCTools), позже cargo-deny 0.20.2. TICKET-07 разблокирован.
2. **TICKET-02 (письмо автору sharkfin)** — вместо черновика письма пользователь распорядился изучить методы sharkfin по коду: «это вполне нормально, ведь проект opensource». В корне рабочей директории обнаружена локальная копия `sharkfin/sharkfin` (v0.3.5). Заведён TICKET-21; TICKET-02 остаётся READY невыполненным.

Отмечено для протокола: разрешение пользователя читать чужой открытый исходник не отменяет ADR-0001. Код GPL-проекта не копируется и не портируется; изучены и переформулированы инженерные решения.

## 2026-08-17 — TICKET-21

### Outcome

DONE

### Work completed

`docs/prior-art/sharkfin-methods.md`: 12 методов к перениманию с обоснованием каждого, 5 осознанных отличий с причинами, наблюдение о масштабе, список изменений в наших артефактах. Изучены транспорт, реестр, слой команд, протокольный модуль, сборочные файлы, CI и Python-инструменты.

### Decisions made

- Три additive-правки в `spec.md`: критерий «переживает выключение питания → это flash-запись» (класс записи задаёт интервал до **и** тишину после); запрет второго экземпляра приложения; уточнение, что stall останавливает и фоновый опрос, а не только текущую команду.
- Два вопроса отнесены в архитектурный чекпоинт перед фазой 3, а не решены на месте: уровень «линия прошивки» между семейством и платой, на котором выдаются права на возможность; класс `origin` для факта о железе, известного только из реестра.

### Deviations

Тикета не существовало — заведён по запросу пользователя вместо действия TICKET-02. Полная запись мутации плана — в `issues/21-*.md`, раздел Origin.

### Root causes discovered

Наблюдение, объясняющее исходную постановку задачи проекта: HE-слой у ROYUAN не «отсутствует», а реализован неодинаково на разных платах одного семейства. Ниша существует из-за этой неоднородности, и именно она делает многосигнальный fingerprint с per-board evidence обязательным.

### Verification

Ручная: конспект самодостаточен для применения решений в TICKET-07/11/18 без повторного чтения чужого кода. 6 пунктов самопроверки PASS.

### Review result

Блокирующих findings нет.

### Architecture observations

Sharkfin покрывает сопоставимый объём одним крейтом на ~3400 строк; у нас запланировано девять крейтов. Совпадает с п.2 нашего architecture review о риске переусложнить границы до первого работающего engine. Вывод: поднять все крейты по DAG (дёшево фиксирует направление), но не наполнять типами; вопрос объединения — на чекпоинте между фазами 3 и 4, когда появятся данные.

Независимое подтверждение решения `DeviceSession`/worker: у sharkfin мьютексная схема, и она работает ровно потому, что у них нет непрерывного аналогового стрима.

### New risks

Наш собственный второй экземпляр приложения — источник того же конфликта за endpoint, что и вендорский софт. Внесено в `spec.md`, закрыто в TICKET-07 (single-instance).

### Follow-up work

TICKET-02 по-прежнему в очереди.

### Next eligible ticket

TICKET-07 (разблокирован).

## 2026-08-17 — TICKET-07

### Outcome

DONE_WITH_DEVIATIONS

### Work completed

Cargo workspace с девятью крейтами-заглушками по DAG, Tauri 2 + React 19 скелет с полным трёхмеханизменным IPC-контрактом, `tools/{emu,protodoc}` как члены workspace, `tools/ingest` отдельным workspace, `data/` с README и тремя директориями, `deny.toml`, CI на трёх ОС с отдельными джобами для лицензий/изоляции/DAG, `scripts/check_crate_dag.py`, `rust-toolchain.toml`, `.gitattributes`, `README.md`, иконки. `git init` + первый коммит `f770750`, включающий также артефакты TICKET-01/03/04/21.

### Decisions made

- Проверка направления DAG автоматизирована вместо ручной сверки (тикет предполагал ручную).
- Изоляция `tools/ingest` проверяется тремя независимыми способами, включая требование, чтобы исключённый крейт всё же компилировался отдельно.
- Channel-команда зарегистрирована и явно возвращает «не реализовано», а не отсутствует — контракт должен быть настоящим с первого дня.
- `hidapi` 2.6.6 резолвится как чистый MIT; заготовленное исключение в `deny.toml` убрано, потому что исключение заменяет allow-list для крейта и создаёт дыру.
- Ошибка старта Tauri обрабатывается явно (лог + stderr + exit 1) вместо panic.
- Мобильные наборы иконок удалены: продукт десктопный.

### Deviations

CI написан, но не исполнялся — нет remote-репозитория, а его создание публикует код проприетарного проекта и требует решения пользователя. Плюс автоматизация DAG-проверки (усиление) и несколько инфраструктурных файлов вне явного scope. Полные записи — в `issues/07-*.md`.

### Root causes discovered

Первая установка VS Build Tools упала с exit 5008 из-за параллельной winget-установки rustup. Повтор по одному прошёл. Зафиксировано в MASTER_PLAN как замечание по среде.

### Verification

Локально на Windows: `cargo build/test/fmt/clippy` (clippy с `-D warnings`), `cargo deny check licenses` и `bans sources` (обе exit 0), DAG-скрипт, три проверки изоляции `ingest`, `npm run typecheck`, `npx vite build`, `npx tauri dev` (окно «Peripheral»), повторный запуск бинаря (второй процесс не создался). Полная таблица — в тикете.

Не проверено: прогон CI на трёх ОС.

### Review result

Блокирующих findings нет. Одно замечание передано в TICKET-08: `hidapi::HidError` протекает в публичный тип ошибки транспорта как источник `#[from]`. Это тип ошибки, не handle, доступа к устройству он не даёт — но за границей стоит следить.

### Architecture observations

Три из четырёх findings уровня REQUIRED_BEFORE_IMPLEMENTATION закрыты (изоляция `ingest`, направление DAG, трёхканальный IPC). Четвёртый (`DeviceSession`/worker) закрыт в части публичного контракта: `DeviceId`/`SessionHandle` объявлены, фактическая worker-модель — TICKET-08.

Собственный lint (`clippy::expect_used`) поймал реальный дефект в первый же прогон, что оправдывает включение этих трёх lint'ов на весь workspace до появления кода.

### New risks

Кроссплатформенность держится на конфигурации, а не на прогоне. Конкретные предположения, которые проверит первый CI: `libudev-dev` для сборки `hidapi` на Ubuntu и набор webkit2gtk-зависимостей Tauri. До первого прогона ни один тикет не вправе считать «CI green» доказанным.

### Follow-up work

Создание remote-репозитория и первый прогон CI — требует решения пользователя. Артефакт third-party notices (из TICKET-03) по-прежнему без тикета до первой инкорпорации permissive-кода.

### Next eligible ticket

TICKET-08 (Windows HID inventory на AULA Hero 84 HE) — плата в наличии, блокеров нет. Параллельно доступны TICKET-11 (`psafety` skeleton) и TICKET-05 (OEM-карта).

## 2026-08-17 — Phase 2 перестроена: ROYUAN-платы не будет

### Решение пользователя

ROYUAN-плата не покупается. Вторым референсным устройством становится уже имеющаяся полноразмерная EPOMAKER. Фаза 2 перестраивается не вокруг «найти второе семейство любой ценой», а вокруг двух реально имеющихся устройств.

Ключевое условие, поставленное пользователем: **EPOMAKER заранее не считается ни ROYUAN, ни вообще вторым protocol family** — это должно доказать железо.

### Мутации плана (правила «Cancelling a ticket» / «Changing acceptance criteria»)

**TICKET-06 — заменён полностью.** Было: «Купить ROYUAN HE-клавиатуру». Стало: «Идентификация референсной платы EPOMAKER» (`issues/06-identify-epomaker-board.md`, файл переименован через `git mv`, прежний текст в истории).
- Почему прежний не нужен: покупка отменена решением пользователя.
- Чем заменён: определить модель и ревизию, HE или механика, тип подключения, официальный конфигуратор, заявленные функции, признаки известного OEM.
- Существенная разница, а не косметическая: прежний тикет покупал устройство с *заранее предполагаемым* семейством (ROYUAN по VID). Новый ничего не предполагает — принадлежность и есть предмет исследования.

**TICKET-09 — заменён полностью.** Было: «HID-инвентарь ROYUAN-платы». Стало: «HID-инвентарь EPOMAKER + сравнение двух устройств» (`issues/09-windows-hid-inventory-epomaker.md`, переименован через `git mv`).
- Требование, добавленное явно: инвентарь снимается **тем же `ptransport::enumerate()`**, что и для AULA. Отдельная ветка кода «под EPOMAKER» запрещена; если она понадобилась — это находка о транспорте, которую исправляют в транспорте, а не обходят в тикете.
- Добавлен обязательный артефакт, которого раньше не было: **сравнительный отчёт двух устройств** (VID, PID, число TLC, vendor TLC, usage page, размер report'а, хеш дескриптора, feature reports, аналоговый интерфейс, конфигуратор).
- Acceptance criteria не ослаблены: прежнее требование (полный TLC-инвентарь тем же форматом) сохранено, к нему добавлены четыре обязательных явных ответа и сравнительный отчёт.

**TICKET-05 — изменено назначение, не scope-минимум.** Раньше карта нужна была, чтобы выбрать плату для покупки. Теперь — как база для fingerprinting и чтобы понять, куда потенциально относятся AULA и EPOMAKER. Добавлено требование таблицы платформ с различающими признаками (а не только списка моделей) и отдельных карточек обоих физических устройств.

**TICKET-08 — уточнён, не изменён по существу.** Явно зафиксирован полный перечень снимаемых полей (дословно совпадающий с TICKET-09, чтобы отчёты сравнивались построчно) и четыре вопроса, требующих явного ответа: есть ли config vendor TLC, есть ли analog vendor TLC, одна это коллекция или разные, нужен ли Win32 escape hatch. Плюс усиленный запрет на любые write-команды.

**Фаза 5 переименована** с «ROYUAN-engine» на «Second protocol family»: какое семейство станет вторым, решают данные фазы 2, а не старый план покупки.

### Почему это соответствует спецификации, а не противоречит ей

Спецификация уже требует многосигнального матчинга и прямо запрещает выводить protocol family из VID:PID. Прежний план при этом *сам* исходил из предположения о семействе будущей покупки («ROYUAN по VID»). Новая постановка убирает это противоречие: сначала evidence, потом присвоение family.

### Четыре исхода, все полезные

A. другой HE-протокол → second-family reference. B. тот же или OEM-родственный → проверка fingerprinting на двух моделях одного семейства. C. обычная механика → generic HID/transport test device, но второй HE-family не заменяет. D. нет доступного config-интерфейса → негативный тест: приложение обязано сказать «config-интерфейс недоступен», а не писать наугад.

### Правки спецификации (все additive или уточняющие, ослаблений нет)

- § Acceptance criteria AC2/AC3: второе устройство — EPOMAKER; инвентарь снимается одним кодом транспорта; добавлено требование сравнительного отчёта; явный запрет выводить семейство из бренда; отсутствие config-интерфейса объявлено валидным результатом.
- § Open questions: решение о референсном железе переформулировано; **Q3 пересмотрен** — тип подключения EPOMAKER неизвестен, и если она беспроводная, wireless/battery-трек перестаёт быть заблокированным до фазы 6.
- Приложение A: `dev.connection` больше не утверждает «для ROYUAN/AULA-класса всегда `usb`» — для второго устройства это открытый вопрос TICKET-06.
- § Test seams: hardware-in-the-loop чеклист — оба устройства проходят один и тот же чеклист одним и тем же кодом.

### Новый риск взамен снятого

Снят: «не хватает железа, ждём доставки». Появился: EPOMAKER может не дать второго HE-семейства (исходы C и D), и тогда `ProtocolEngine` дойдёт до фазы 4 провалидированным на одном семействе — это риск №3 architecture review («premature trait finalization»), и он остаётся открытым, а не закрытым перестройкой фазы.

### Следующее действие

TICKET-08 (AULA inventory), затем TICKET-06 → TICKET-09 → сравнение. TICKET-05 и TICKET-11 идут параллельно.

## 2026-08-17 — TICKET-08

### Outcome

DONE_WITH_DEVIATIONS

### Work completed

`ptransport::inventory` (`Hid`, `HidCollection`, `CollectionAccess`, `enumerate()`, `inspect()`) + dev-инструмент `examples/inventory.rs`, снимающий полный read-only инвентарь любого устройства по VID:PID и пишущий машиночитаемый артефакт схемы `peripheral.hid-inventory/1` плюс markdown. Формат задокументирован в `docs/hardware/README.md`. Снят инвентарь AULA Hero 84 HE.

### Decisions made

- `TransportError::Hid(#[from] hidapi::HidError)` заменён на `Backend(String)` — исполнение замечания ревью TICKET-07. Ни один тип `hidapi` больше не виден снаружи крейта.
- Серийный номер по умолчанию не попадает в артефакт; пишется только факт его наличия.
- Парсер report-дескрипторов оставлен в примере, вне продуктовых крейтов: продуктовый читает недоверенный вход и приезжает с фаззингом в TICKET-10.
- Win32 escape hatch **не** добавлен: пробел `hidapi` зафиксирован как находка, решение отложено до первого реального I/O.

### Данные железа

VID `0x372E`, PID `0x103E`, `BY Tech` / `HERO 84 HE`, release `0x0216`. 7 top-level collections на 3 USB-интерфейсах, **все открылись без прав администратора**:

```text
if0  0001:0006  boot keyboard      rid -   in 8,  out 1
if1  0001:0002  mouse              rid 2   in 5,  feature 2
if1  0001:0006  keyboard NKRO      rid 7   in 16
if1  0001:0080  system control     rid 5   in 2
if1  000C:0001  consumer control   rid 4   in 3
if2  FF00:0001  vendor-defined     rid 3   feature 64          ← только feature
if2  FF60:0061  vendor-defined     rid 9   in 64 / out 64      ← только in/out
```

### Root causes discovered

Не диагностический тикет, но одно наблюдение объясняет прежнюю неопределённость: у платы **два** vendor-канала с разной механикой обмена, а не один. Предположение «vendor-TLC = канал конфигурации» некорректно уже на первом же реальном устройстве — их два, и какой из них конфигурационный, из дескрипторов не следует.

### Deviations

Четыре, полные записи в `issues/08-*.md`: ответ «не удалось определить» на вопрос об аналоговом TLC (требует I/O, запрещённого тикетом); проверка под elevated не выполнялась (условие не наступило); формат результата шире «дампа в журнал» (нужна построчная сравнимость с TICKET-09); изменён публичный тип ошибки транспорта.

### Verification

`cargo fmt --check`, `clippy -D warnings`, `build --workspace`, `test --workspace` (2 passed), `cargo deny check licenses bans sources` (exit 0), `check_crate_dag.py` — все зелёные. Прогон на железе: 17 коллекций перечислено, 7 инспектировано. Полнота сверена через `Get-PnpDevice`: 3 интерфейса → 1+4+2 = 7, совпадает. Отсутствие серийника в артефактах проверено grep'ом.

### Review result

Блокирующих findings нет. Одно незакрывающее замечание: парсер дескрипторов вне fuzz-покрытия — осознанно, продуктовый парсер в TICKET-10.

### Architecture observations

**Модель `DeviceSession` (FR10) требует уточнения формулировки.** Спецификация говорит «одно устройство — одна сессия, владеющая физическим handle». Реальность первого же устройства: одно физическое устройство = 7 открываемых коллекций, и как минимум две из них нужны потенциально одновременно (конфигурация через одну, аналоговый стрим через другую). Сессия владеет **набором** handle'ов, по одному на коллекцию. Существо FR10 не нарушено (handle не покидает крейт, I/O по каждому — в своём worker'е), но текст неточен. **Отнесено в архитектурный чекпоинт перед фазой 3**, не правится сейчас.

Дополнительно: подтверждено, что абстракция `hidapi` покрывает все нужные для инвентаря данные на Windows — перечисление, строки, дескрипторы, открытие. FR7 («`hidapi` как основная абстракция, платформенный код только как escape hatch») пока держится без исключений.

### New risks

**Классификация ошибок backend'а по тексту невозможна в принципе.** `hidapi` отдаёт сообщение без кода, а на Windows это локализованный текст ОС (на этой машине — русский). Prior art определяет stall подстрокой `Protocol error`; на локализованной системе такой тест молча не срабатывает, то есть kill-switch (FR3) просто не сработает. Значит `EndpointStalled`/`AccessDenied` обязаны определяться кодом платформы, и это — первый конкретный кандидат на Win32 escape hatch. Риск активируется в TICKET-12, когда появится реальный I/O.

**Ложное чувство доступа.** `opened = true` на Windows не означает возможности обмена: backend при отказе в read/write открывает устройство с нулевыми правами. Если TICKET-12 прочитает инвентарь как «доступ есть», он будет отлаживать не ту проблему. Зафиксировано в документации типа, в `docs/hardware/README.md` и в тикете.

### Follow-up work

Уточнение формулировки FR10 — архитектурный чекпоинт перед фазой 3. Продуктовый парсер дескрипторов с фаззингом — TICKET-10. Проверка кандидата `0xFF60:0x0061` (пара, совпадающая с соглашением QMK/VIA raw HID) — TICKET-05/10 как гипотеза с низкой уверенностью, не как вывод о прошивке.

### Next eligible ticket

TICKET-06 (идентификация EPOMAKER) — по порядку фазы 2. Параллельно доступны TICKET-05 и TICKET-11. *(Отменено мутацией плана того же дня — см. следующую запись.)*

## 2026-08-17 — Мутация плана: EPOMAKER переведена в remote external validation track

### Причина

**EPOMAKER существует и доступна для будущего тестирования, но физически находится у другого человека** (отца пользователя). Установка developer-tooling и проведение ручного HID-исследования на чужом ПК противоречит цели продукта и не даёт ценного product-level validation. Устройство переносится из reference-hardware track в remote external-validation track. Оно будет исследовано через обычную сборку Peripheral с безопасным read-only inventory export. Это превращает EPOMAKER из второй лабораторной платы в реальный тест сценария будущего пользователя / community submission.

Отдельно и явно: **отсутствие второй локальной платы не блокирует текущий AULA vertical slice.** Риск «архитектура пока проверена только на одном protocol family» **остаётся открытым** и должен быть виден в architecture review; его нельзя молча считать закрытым.

Что прямо исключено этим решением: удалённая установка Rust, cargo, Visual Studio Build Tools, диагностических CLI, USBPcap/Wireshark, dev-сборок, PowerShell/Python-скриптов и ручных HID-команд на чужом компьютере. Максимум, что делает владелец устройства: `скачать → запустить → подключить → Export`.

### Мутации плана

**TICKET-06 — перенесён, не отменён.** Было: «Идентификация референсной платы EPOMAKER», READY, тикет фазы 2. Стало: «Удалённая идентификация EPOMAKER», статус `DEFERRED_REMOTE_VALIDATION`, remote-validation track.
- Порядок развернулся: теперь **09 → 06**. Идентификация ведётся по артефакту, который сгенерирует приложение, плюс публичные источники.
- Acceptance criteria **расширены**, не ослаблены: маркетинговое имя, manufacturer/product strings, VID:PID, `bcdDevice`/release, тип клавиатуры (только если подтверждаем), тип подключения (только если подтверждаем), официальный конфигуратор, заявленные возможности, признаки OEM/controller/platform — **и явная пара confidence + evidence на каждом выводе**.
- Запрет выводов по бренду сохранён и усилен: заранее не назначать EPOMAKER ни вторым protocol family, ни HE, ни ROYUAN, ни конкретным OEM, ни wireless-девайсом.

**TICKET-09 — перенесён и переписан по существу.** Было: developer-side «HID-инвентарь EPOMAKER на Windows + сравнение двух устройств». Стало: «Remote EPOMAKER inventory validation», статус `DEFERRED_REMOTE_VALIDATION`.
- Предметом тикета перестал быть сам инвентарь и стал **сценарий его получения**. Главный acceptance test: Peripheral на чужом Windows-ПК без dev-toolchain самостоятельно получает безопасный read-only инвентарь неизвестной EPOMAKER и экспортирует его в стандартный артефакт, структурно сопоставимый с AULA-инвентарём из TICKET-08.
- Сохранены целиком: набор снимаемых полей, четыре обязательных явных ответа (config TLC / analog TLC / одна коллекция или разные / нужен ли escape hatch), обязательный сравнительный отчёт (VID:PID, release, manufacturer/product, USB interface count, TLC count, usage page/usage, report IDs, input/output/feature sizes, vendor-defined TLC, descriptor fingerprints, доступность коллекций, тип подключения, прочие наблюдаемые сигналы).
- Добавлено: строгий запрет protocol probing и любых write-команд в рамках remote inventory; serial redaction по умолчанию; versioned artifact schema.

**TICKET-05 — вынесен в параллельный research track.** Не является входным гейтом ни одной кодовой фазы. Данные, нужные конкретно TICKET-10, уже добываются из AULA, поэтому реализация не ждёт большой OEM-таблицы. Карточка EPOMAKER в нём понижена до строки с гипотезами из публичных источников.

**Phase 2 переопределена** — «AULA Hardware Reality». Главный hardware-gate пройден TICKET-08. В фазе остаётся только он; 05 уходит в research track, 06/09 — в remote-validation track. Фаза закрыта.

**Основной code path закреплён:** TICKET-11 → 10 → 12 → 13 → 14 → 15 → 16, с TICKET-05 параллельно.
- **11 первым**, потому что после TICKET-08 проект впервые приблизился к настоящему I/O: гарантия `raw opcode ✗ → SafeCommandId ✓ → Safety Gate ✓ → Transport` должна существовать раньше первого protocol engine, чтобы ни один engine не смог случайно обойти safety boundary.
- **10 вторым**, потому что впервые есть настоящие данные AULA (descriptor topology, TLC, VID/PID, strings, report IDs и размеры, release, vendor-коллекции) — `pregistry` строится против реального устройства, а не теоретически.
- **12 третьим** — первый AULA engine, строго read-only, через `DeviceSession`/`pregistry`/capability-модель/safety-границы, не через `hidapi` напрямую.
- **13/14 затем** — нормальный пользовательский путь и эмуляция после появления реального вертикального среза.

**TICKET-17 — блокировка на TICKET-09 снята.** Второе семейство определяется данными; источником может стать remote-артефакт EPOMAKER, community submission или новое устройство. Формулировка «ROYUAN engine» окончательно убрана из обязательных требований (тикет 17, `architecture/INITIAL_REVIEW.md` §3).

**TICKET-10/14 — зависимости от второго устройства понижены до follow-up.** Ни один из них не блокируется remote-треком.

### Архитектурное правило, подтверждённое явно

EPOMAKER проходит через **тот же production pipeline**, что и AULA. Появление `if epomaker { ... }` в транспортном слое ради получения инвентаря запрещено. Если generic `ptransport` не справится — это находка о generic transport abstraction; чинится абстракция/транспорт, а не создаётся vendor-specific enumeration.

### Гейт возврата EPOMAKER — capability-based

Не «после фазы N», а: существует обычная Windows-сборка, которая (1) безопасно enumerate неизвестное устройство, (2) ничего в него не пишет, (3) генерирует стандартный versioned inventory/profile-артефакт, (4) автоматически редактирует серийник и прочие идентификаторы, не нужные для fingerprinting, (5) показывает одну понятную кнопку Export. Естественная точка — после TICKET-13/14 или как ранняя безопасная часть Level 0 из TICKET-16. Форсировать сразу после TICKET-13 не следует: пользователь хочет проверять EPOMAKER на «более-менее готовом продукте», а не на инженерном debug-инструменте.

Полный Learning Mode ради EPOMAKER раньше TICKET-16 не создаётся. Если продуктовому пути понадобится маленький переиспользуемый компонент `Export Device Report`, он выделяется отдельным узким тикетом или ранней частью Inventory Level 0: read-only, без safe probes, без write, без capture пользовательского ввода, с serial redaction по умолчанию и versioned artifact schema.

### Findings TICKET-08, формально записанные в архитектурный чекпоинт

Ничего не исправляется сейчас; всё записано и обязано быть рассмотрено (повестка — в `MASTER_PLAN.md`):

1. **`DeviceSession`.** FR10 говорит «одна сессия владеет физическим handle», а реальное устройство даёт 3 интерфейса → 7 TLC → несколько потенциально нужных handle'ов. Рассмотреть `DeviceSession owns CollectionHandleSet` или эквивалент. Главный инвариант сохраняется: handles никогда не выходят из `ptransport`. Врезка добавлена в `spec.md` FR10; сам текст требования намеренно не переписан.
2. **Ошибки `hidapi`.** Текстовые/локализованные сообщения недостаточны для machine-readable stall detection. Win32 escape hatch **сейчас не реализуется**; вернуться при первом настоящем I/O (TICKET-12/15) и только тогда решить, нужна ли platform-native классификация ошибок.
3. **`opened = true`.** `descriptor/enumeration access` ≠ `usable read/write config channel`. Не повышать confidence на том основании, что коллекция открылась для метаданных.
4. **Serial privacy.** Политика стала **общей**, не AULA-специфичной: `serial present: true` / `serial value: REDACTED` по умолчанию во всех передаваемых артефактах; сырой серийник — только по явному debug/export opt-in. Внесено в `spec.md` § Domain rules + новый Test seam «Redaction по построению».

### Правки спецификации (уточнения и ужесточения, ослаблений нет)

- AC1: EPOMAKER исключена из числа референсных устройств; единственная reference board — AULA.
- AC2: второе устройство больше не участвует в Phase 1 DoD.
- AC3 разделён на **AC3a** (TICKET-08, выполнен) и **AC3b** (TICKET-09, remote external validation с явным требованием «без dev-toolchain у владельца» и запретом vendor-specific ветки enumeration).
- Q2: уточнены источники наблюдений.
- Q3: остаётся открытым дольше; EPOMAKER заранее беспроводной не считается; capability-модель `dev.power.*` не меняется.
- Domain rules: новая общая политика редактирования идентификаторов экземпляра.
- FR7/FR10: врезки с findings TICKET-08.
- Test seams: hardware-in-the-loop переформулирован (удалённая часть исполняется владельцем через обычную сборку); добавлен seam «Redaction по построению».
- Приложение A: `dev.connection` для EPOMAKER устанавливается удалённо.

### Изменение риск-регистра

Риск «второе устройство может не дать второго protocol family» переформулирован и **усилен** в «архитектура валидирована на одном устройстве и одном protocol family»: второй платы на столе разработчика нет вообще, а эмулятор собран из записей самой AULA и потому не способен опровергнуть AULA-специфичные предположения. Добавлен новый риск: remote-валидация может не состояться или дать неполные данные (зависит от постороннего человека и от понятности UX) — неудача сценария при этом является валидным результатом о продукте.

### Следующее действие

TICKET-11 (`psafety` ACL + `SafeCommandId` skeleton). Блокеров нет.

## 2026-08-18 — TICKET-11

### Outcome

DONE_WITH_DEVIATIONS

### Work completed

`psafety` из заглушки превращён в работающую границу: ACL по семействам в `data/protocols/*.toml` (схема `peripheral.opcode-acl/1`), build-time кодоген закрытого `SafeCommandId`, rate limiter на измеренных таймингах, journal без полезной нагрузки, kill-switch, требование backup перед записью. Модули: `class`, `command`, `rate`, `journal`, `gate` + генератор `codegen.rs`, включаемый и в `build.rs`, и в тесты, чтобы тестировался ровно тот код, который исполняется при сборке.

### Decisions made

- **Гейт владеет sink'ом, а не одалживает его.** Engine держит `&mut SafetyGate` и не имеет объекта, через который можно отправить байт мимо. Вместе с приватным конструктором `AuthorizedCommand` и `pub(crate)`-видимостью `opcode()` это и есть граница: engine способен выразить «выполни одобренную команду», но не «отправь этот байт».
- **Гейт оперирует `DeviceId`, а не `SessionHandle`.** `SessionHandle` невозможно сконструировать вне `ptransport`, а трогать транспорт в этом тикете было запрещено. Побочно вышло строже по FR10: сессиями владеет sink, второго места владения устройством не появилось.
- **ACL в TOML, а не YAML.** Парсер ACL — часть границы безопасности: неверно разобранная запись это дефект безопасности, а не форматирования. Нужен поддерживаемый парсер с `deny_unknown_fields`, чтобы опечатка в имени поля роняла сборку; единственный YAML-парсер экосистемы помечен deprecated.
- **`trybuild` не использован.** Он сверяет вывод компилятора со снапшотом, а `rust-toolchain.toml` пинит канал `stable`, не версию: снапшот протухнет на первом апдейте и уронит CI по причине, не связанной с безопасностью. Для набора, чья ценность в доверии, размен плохой.
- **ROYUAN-семейства внесены в реестр** (факты из `docs/prior-art/royuan.md`, не код). Без реальных данных кодоген проверялся бы только на синтетике; деструктивные опкоды ROYUAN — самый честный доступный тест «эти байты не должны выжить». Ни движка, ни платы нет, присутствие в ACL ничего не разрешает.
- **Backup заложен как факт, не как содержимое.** Write-класс без записанного backup отклоняется; что внутри backup и как его восстанавливать — не выдумывалось, это TICKET-15.

### Что получилось в реестре

16 команд: 8 на `royuan-gen2`, 8 на `royuan-yc500`, **все `safe_read`/`probe_ok`**. Write-команд — ноль во всём реестре. У `aula-hero84-he` — ноль команд вообще: семейство объявлено, опкодов нет, потому что после TICKET-08 о содержимом vendor-каналов не известно ничего. Отсюда механическая, а не дисциплинарная гарантия: этот тикет не мог записать что-либо в нашу плату.

Правила, которые build script исполняет отказом (а не предупреждением): нет класса / незнакомый класс / незнакомое поле / дубль опкода или имени внутри семейства / нет note / write-класс на evidence слабее собственного железа / read-класс на vendor-JS / класс без измеренного тайминга / `slow_flash` с нулевым settle / половина burst-лимита / незнакомая схема.

### Root causes discovered

Не диагностический тикет, но одно наблюдение стоит записи: **правило «нет измеренного тайминга — нет команды» оказалось сильнее, чем задумывалось.** Оно автоматически не даёт классу заимствовать соседнее число: у `royuan-gen2` измерены read и flash, но не `safe_write`, — и `safe_write` там попадает в ветку «неизмеренное семейство» с требованием подтверждения, а не подхватывает 12 мс от чтения. Это ровно то поведение, которое хотелось получить, но получено оно свойством схемы, а не отдельной проверкой.

### Deviations

Пять, полные записи в `issues/11-*.md`. Существенные — отказ от `trybuild` и TOML вместо YAML; остальные три (ROYUAN в реестре, `DeviceId` вместо `SessionHandle`, backup как факт) архитектурно строже исходной формулировки, а не слабее.

### Verification

`cargo fmt --all --check`, `clippy --workspace --all-targets -D warnings`, `build --workspace`, `test --workspace` (65 passed, 64 из них `psafety`), `cargo deny check licenses bans sources` (`bans ok, licenses ok, sources ok`), `check_crate_dag.py` (`12 crates`) — все зелёные.

Отдельно, вручную: (1) сборка **падает** при попытке внести `0xAC` как `safe_write` — сработали два независимых правила подряд (дубль опкода, затем evidence), сообщение называет файл, опкод и что делать вместо этого; (2) мутационная проверка тестов гейта — снятие family-check роняет 1 тест, снятие карантина 3; (3) в сгенерированном `safe_command_id.rs` нет ни одного деструктивного опкода ни как варианта, ни как литерала.

### Review result

Блокирующих findings нет. Два незакрывающих замечания, оба записаны в тикет и в doc-комментарий крейта, чтобы не выглядели закрытыми:

1. **Последний участок пути держится на соглашении.** `ptransport` не имеет write-API; когда он появится (TICKET-12/15), он обязан принимать только продукт гейта. Сегодня sink, реализованный поверх `DeviceSession`, технически может писать мимо.
2. **`build.rs` кладёт `data/protocols` на граф сборки** — крейт зависит от layout'а репозитория выше себя. План отхода (xtask + закоммиченный файл + проверка в CI) записан прямо в `build.rs`.

### Architecture observations

Kill-switch реализован **только** на типизированном `EndpointStalled`. Это переносит ответственность в транспорт: если он не сумеет типизировать stall, kill-switch не сработает вовсе. Записано в риск-регистр как изменение адресата риска (TICKET-11 → TICKET-12/15), а не как его закрытие. Тест с русскоязычным сообщением backend'а фиксирует, что текст не разбирается нигде.

Правило коллизии опкодов из `spec.md` впервые исполняется машиной, а не документом: `Refusal::WrongFamily`. Пара gen2/yc500 в реестре — живой пример, на котором это проверено.

Требование «write только при `confidence >= Verified`» в гейте пока **не** выражено: у binding'а устройства есть семейство, но нет confidence — это словарь `pregistry`. Сегодня правилу нечего разрешать (write-команд ноль), но TICKET-10 обязан довезти confidence в `identify_device`, иначе к TICKET-15 останется дыра.

### New risks

Ничего нового. Два существующих риска изменили статус: «окирпичивание записью» существенно снижен (остаточная часть — write-API транспорта), «классификация ошибок по тексту» сменил адресата на TICKET-12/15.

### Next eligible ticket

TICKET-10 (`pregistry` + многосигнальный fingerprinting) — данные AULA из TICKET-08 есть, `psafety` готов принять confidence. Параллельно доступен TICKET-05.

## 2026-08-18 — Мутация плана: TICKET-22 (VXE Dragonfly R1 SE+) вставлен перед TICKET-10

### Причина

У разработчика локально появилось **второе физическое устройство, и оно другой категории** — беспроводная мышь VXE Dragonfly R1 SE+ с 2.4 ГГц ресивером (вариант `44857375424730`, официальная страница ATK). Решение пользователя: не ждать фазы 6, а снять её read-only инвентарь немедленно, перед TICKET-10.

Обоснование конкретное, а не «раз есть железо, давайте посмотрим». TICKET-10 проектирует `pregistry` и многосигнальный fingerprinting, и до сих пор в его распоряжении была ровно **одна** topology: проводная клавиатура на трёх USB-интерфейсах с семью TLC. Модель, спроектированная против одного примера, почти наверняка окажется keyboard-specific в местах, которые никто не заметит, пока не появится второе устройство, — то есть до фазы 6. Это ровно риск №3 architecture review («premature trait finalization»), только применительно к реестру.

Мышь плюс ресивер дают минимум три реальные topology и ставят перед реестром вопросы, которых на одной клавиатуре не существует:

```text
одно физическое устройство, разные VID:PID по режимам → одна запись или несколько?
ресивер и мышь                                        → два устройства или одно составное?
какие сигналы переживают смену режима подключения, а какие нет?
```

Побочно это первый настоящий тест утверждения «fingerprint идентифицирует устройство»: одно и то же физическое устройство система видит по-разному в разных режимах.

### Мутации плана

**Создан TICKET-22** — «HID-инвентарь VXE Dragonfly R1 SE+ во всех режимах подключения», READY. Строго read-only, аналогичен TICKET-08 по методу и формату артефакта (`peripheral.hid-inventory/1`), снимается тем же `ptransport::enumerate()`.

Режимы: 2.4 ГГц через штатный ресивер (обязательно), проводной USB (обязательно), Bluetooth — **только если этот экземпляр его реально поддерживает**; поддержка не предполагается, она наблюдается или её отсутствие фиксируется.

Семь обязательных вопросов: самостоятельное ли HID-устройство ресивер; есть ли у ресивера vendor-defined config TLC; отличается ли проводная topology мыши от topology ресивера; виден ли за ресивером отдельный логический device; есть ли наблюдаемый стандартный battery HID usage; отличается ли fingerprint одного устройства между режимами; какие данные относятся к ресиверу, а какие к мыши.

Обязательный артефакт: сравнительный отчёт `AULA vs VXE wired vs VXE receiver [vs VXE BT]`.

**Границы зафиксированы явно, чтобы тикет не разросся:**

- никаких write, feature-чтений, input-репортов и probe — инвентарь, и только;
- никакой VXE-специфичной ветки в `ptransport`: понадобилась ветка → это находка о generic transport abstraction, чинится абстракция;
- полный `MouseProtocol` и любые записи DPI/polling/кнопок **не переносятся** из фазы 6;
- находка о доступном battery state оформляется отдельным read-only follow-up тикетом, а не расширением TICKET-22.

**Phase 2 переоткрыта и переименована** из «AULA Hardware Reality» в «Hardware Reality»: состав — TICKET-08 (DONE) + TICKET-22. Закрывается после TICKET-22.

**TICKET-10 обновлён.** Зависимость от TICKET-22 добавлена; в scope добавлены записи VXE по каждой наблюдавшейся topology (или одна составная — что правильно, решает сам тикет по данным); в acceptance criteria добавлены поведение matcher'а на одном устройстве в разных режимах, различимость ресивера и устройства за ним, и обязанность довезти confidence до `SafetyGate::identify_device`.

**Порядок основного code path:** 22 → 10 → 12 → 13 → 14 → 15 → 16.

### Правки спецификации

- AC1: локальное железо теперь два устройства, второе — беспроводная мышь.
- **Q3 существенно изменён.** Он был записан исходя из того, что ни одно доступное устройство не беспроводное. Теперь беспроводное устройство есть локально, и `dev.connection` впервые получает устройство, у которого значение зависит от режима. Но вопрос **не закрыт**: беспроводное подключение само по себе не означает читаемый заряд. Наличие стандартного battery HID usage — предмет наблюдения TICKET-22; вендорский путь потребовал бы probe и лежит вне инвентаря.
- Приложение A, `dev.connection`: отмечено первое устройство с несколькими значениями.
- Test seams, hardware-in-the-loop: чеклист теперь два устройства, мышь проходит его в каждом режиме подключения.

### Изменение риск-регистра

Риск «архитектура валидирована на одном устройстве и одном protocol family» **частично смягчён**, и важно не смягчить его больше, чем следует: TICKET-22 закрывает его для `pregistry` и `ptransport` (три topology вместо одной, вторая категория устройств), но **не** для `ProtocolEngine` — второго protocol family по-прежнему нет, и мышь его не даёт, потому что тикет read-only и без протокольного слоя. Engine-уровень риска остаётся открытым в прежней формулировке.

### Следующее действие

TICKET-22. Требует физического участия: подключить мышь в каждом режиме по очереди. Блокеров нет, инструмент существует с TICKET-08.

## 2026-08-18 — TICKET-22

### Outcome

DONE_WITH_DEVIATIONS

### Work completed

Сняты два режима подключения VXE Dragonfly R1 SE+ штатным инвентарь-инструментом TICKET-08, без единой строчки кода под это устройство: 2.4 ГГц через ресивер (`3554:F58E`, `Compx` / `VXE Mouse 1K Dongle`, release `0x0110`) и проводной USB (`3554:F58F`, `Compx` / `VXE R1SE+`, release `0x0315`). Оба — 8 top-level collections на 3 USB-интерфейсах, **четыре** vendor-defined. Плюс сравнительный артефакт `docs/hardware/comparison-aula-vs-vxe.md`.

Дополнительно ресивер снят трижды — при активно работающей по радио мыши, при мыши на кабеле и при полностью выключенной мыши.

### Данные железа

```text
                 AULA            VXE receiver        VXE wired
VID:PID          372E:103E       3554:F58E           3554:F58F
strings          BY Tech /       Compx /             Compx /
                 HERO 84 HE      VXE Mouse 1K Dongle VXE R1SE+
release          0x0216          0x0110              0x0315
интерфейсы       3               3                   3
TLC              7               8                   8
vendor-TLC       2 (64 байта)    4 (17/8/8/8)        4 — те же
battery usage    нет             нет                 нет
```

### Root causes discovered

**Главная находка тикета — не про мышь, а про модель fingerprint'а.**

Ресивер и сама мышь отдают **побайтово одинаковые дескрипторы**: все восемь коллекций, те же usage page/usage, те же report ID и размеры, те же восемь хешей. Между двумя режимами различаются ровно три поля: PID, product string и release.

Спецификация ранжирует сигналы от сильного к слабому: хеш дескриптора → набор TLC → строки → identify-опкод → версия прошивки → VID:PID («слабейший, только индекс»). На этом железе порядок **перевёрнут**: два сильнейших сигнала не различают ресивер и мышь вообще, а различают ровно три слабейших.

Правильный вывод не «ранжирование неверно», а «ранжирование неполно»: сильные сигналы отвечают на вопрос *«что это за устройство по форме, каким протоколом оно может говорить»*, слабые — на вопрос *«с каким конкретно физическим предметом я сейчас разговариваю»*. `pregistry` нужны оба ответа, и это разные веса. Занесено в риск-регистр и в acceptance criteria TICKET-10 — риск закрывается его работой, а не сам собой.

Вторая находка, поменьше: ресивер — полностью самостоятельное HID-устройство. Три захвата в трёх состояниях мыши (работает по радио / лежит на кабеле / выключена) не отличаются ни одним полем. Он показывает mouse-коллекцию, keyboard-коллекцию и четыре vendor-канала независимо от наличия мыши. Практическое следствие для UI: список устройств, построенный на одном перечислении, покажет донгл как всегда присутствующую мышь, и отличить «ресивер с подключённой мышью» от «ресивер один» без probe невозможно.

### Deviations

Три, полные записи в `issues/22-*.md`. Существенная одна: **Bluetooth не снят и не опровергнут.** BT-позиция переключателя у мыши есть, спарить с этим ПК не удалось. Конфаунд исключён: BT-стек хоста рабочий, `Generic Bluetooth Radio` присутствует, с ним спарены другие устройства, — значит отсутствие BT HID у мыши не объясняется отсутствием радио на машине. Утверждение «этот экземпляр BT не поддерживает» сознательно **не** сделано: наблюдение inconclusive, и записано именно так. Acceptance criterion предусматривал два исхода, фактический оказался третьим, и он зафиксирован как есть, а не подогнан под ближайший.

Остальные две: два вспомогательных захвата ресивера не коммитились (три почти одинаковых файла в `docs/hardware/` были бы шумом — сам факт и метод записаны); в `docs/hardware/README.md` добавлено соглашение о `comparison-*.md`, потому что README прямо запрещал держать выводы в этом каталоге, а тикет требовал сравнительный артефакт.

### Verification

Полнота обоих захватов сверена через `Get-PnpDevice`: MI_00 + MI_01 (COL01–COL06) + MI_02 = 8 коллекций, совпадает с перечислением. Серийники отсутствуют в артефактах (`serial_number: null`, `serial_number_present: true`); дополнительно проверено поиском в файле того значения, которое Windows показывает на USB-узле проводной мыши, — не найдено. Наличие стандартного battery usage проверено не только по usage page коллекций, но и поиском байтов Usage Page Battery System (`0x85`) и Power Device (`0x84`) внутри всех дескрипторов всех трёх устройств — ноль вхождений.

Отдельной ветки кода под устройство не потребовалось: оба режима и все три состояния сняты штатным `ptransport::enumerate()`. Находок о generic transport abstraction нет — **транспорт впервые проверен на устройстве другой категории и не потребовал изменений.**

### Review result

Блокирующих findings нет. Два незакрывающих: кандидат `FF02:0002` в конфигурационный канал — гипотеза (единственный двунаправленный vendor-канал), проверка требует обмена; вопрос о заряде закрыт только со стороны стандартного HID — «стандартного usage нет» не означает «заряд недостижим», у мышей на ресивере он обычно берётся вендорским опкодом или запросом к самому ресиверу.

### New risks

Один новый: **порядок силы fingerprint-сигналов из спецификации неполон** (см. Root causes). Адресат — TICKET-10.

Снятого риска нет, но один ослаб фактически: generic `ptransport` отработал на устройстве другой категории без единого изменения — это первое свидетельство его generic-ности, полученное не на клавиатуре.

### Next eligible ticket

TICKET-10 (`pregistry` + многосигнальный fingerprinting) — теперь с данными по трём topology и с конкретным вопросом, который обязан решить.
