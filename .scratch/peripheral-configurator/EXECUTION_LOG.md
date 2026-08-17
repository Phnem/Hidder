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
