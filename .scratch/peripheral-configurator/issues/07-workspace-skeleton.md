# TICKET-07: Cargo workspace + Tauri skeleton + CI

## Status

DONE_WITH_DEVIATIONS (2026-08-17) — все acceptance criteria выполнены локально; «CI green на трёх ОС» проверено только конфигурацией и локальным прогоном тех же команд на Windows (нет remote-репозитория, GitHub Actions не запускался). См. Deviations.

## Objective

Поднять cargo workspace со всеми крейтами-заглушками (`ptransport`, `pcaps`, `pproto`, `pregistry`, `psafety`, `pprofile`, `plearn`, `pjournal`, `pcore`) и Tauri+React app-скелетом; CI на трёх ОС (Windows/Linux/macOS) с первого дня, включая лицензионный линтер зависимостей.

## User or system value

Enabling-тикет: без него ни один последующий Phase 1 тикет не имеет, куда писать код. Также единственная точка, где легко гарантировать архитектурные инварианты с нуля (изоляция `tools/ingest`, запрет GPL-зависимостей) — вставлять их постфактум дороже.

## Dependencies

TICKET-04 (лицензионная политика уже зафиксирована — нужна, чтобы настроить deny-list в CI).

## Scope

- `Cargo.toml` workspace, крейты-заглушки по структуре §3.2 плана (пустые `lib.rs` с модульными комментариями-заглушками, не реализацией).
- **Направление `[dependencies]` между крейтами задаётся сразу по DAG из `architecture/INITIAL_REVIEW.md` §9** (`ptransport` → `pcaps`/`pregistry`/`psafety` → `pproto` → `pprofile`/`plearn`/`pjournal` → `pcore` → `app`), даже если сами крейты пока пустые — это единственный дешёвый момент зафиксировать направление зависимостей до того, как в них появится код.
- `tools/ingest` — **отдельный** workspace, не входящий в `[workspace.members]` основного репозитория, либо явно исключён из `default-members` — см. architecture review п.6 (REQUIRED_BEFORE_IMPLEMENTATION).
- `app/` — Tauri + React skeleton (пустые экраны-заглушки, без реальной функциональности); IPC-слой закладывается сразу с тремя механизмами (commands/events/channels — см. architecture review §6 REQUIRED_BEFORE_IMPLEMENTATION, `spec.md` FR11), даже если на этом тикете реальных Channel-стримов ещё нет.
- `tools/emu`, `tools/protodoc` — крейты-заглушки (реализация в TICKET-14 и позже).
- `data/` — пустые директории `devices/`, `layouts/`, `protocols/` с `.gitkeep` или README.
- `ptransport` берёт `hidapi` как основную зависимость (см. `spec.md` FR7); зарезервировать место под platform-specific escape-hatch модуль (`ptransport::platform::windows`/`::linux`/`::macos`), даже пустой на этом шаге.
- CI (GitHub Actions или аналог) на Windows/Linux/macOS: `cargo build --workspace`, `cargo test --workspace`, `cargo-deny check licenses` с deny-list GPL-2.0/GPL-3.0/AGPL-3.0/LGPL (см. ADR-0001).
- `git init` репозитория (в текущей директории пока нет `.git`).

## Out of scope

- Любая реальная бизнес-логика внутри крейтов — это последующие тикеты.
- Публикация/релизный пайплайн — не Phase 1.

## Acceptance criteria

- [x] `cargo build --workspace` проходит — локально на Windows; CI-матрица на трёх ОС сконфигурирована (`.github/workflows/ci.yml`), но не исполнялась: нет remote-репозитория. См. Deviations.
- [x] `cargo-deny check licenses` настроен и проходит (`deny.toml`, exit code 0).
- [x] `tools/ingest` физически не собирается при обычном `cargo build --workspace` — проверено тремя способами локально и зафиксировано отдельной CI-джобой `isolation`.
- [x] Tauri-приложение запускается локально (пустой UI) — Windows 11, процесс `peripheral-app` с окном «Peripheral».
- [x] Репозиторий инициализирован как git-репозиторий с первым коммитом (`f770750`).
- [x] Ни один крейт-заглушка не имеет `path`-зависимости, идущей "вверх" по DAG — проверка автоматизирована (`scripts/check_crate_dag.py`, отдельная CI-джоба) вместо ручной сверки.

## Verification plan

CI green на всех трёх ОС; ручной запуск `cargo tauri dev` (или аналог) для проверки, что приложение стартует.

## TDD classification

NOT_NEEDED (skeleton/infrastructure — нет бизнес-логики для TDD)

## Expected architecture impact

Формирует все дальнейшие границы крейтов. См. `architecture/INITIAL_REVIEW.md` — этот тикет обязан закрыть три из четырёх REQUIRED_BEFORE_IMPLEMENTATION финдингов: (1) изоляция `tools/ingest`; (2) направление crate-DAG (§9) зафиксировано в `Cargo.toml` до появления кода; (3) трёхканальный IPC-контракт (commands/events/channels) заложен в `app/`-скелете. Четвёртый финдинг — `DeviceSession`/worker-модель — закрывается фактически в TICKET-08 (первый реальный код `ptransport`), но публичный контракт (`DeviceId`/`SessionHandle`, не голый handle) должен быть объявлен как типы-заглушки уже здесь, чтобы TICKET-08 с первой строчки писал код против него, а не против голого `hidapi::HidDevice`.

## Risks

- Слишком ранняя фиксация форм крейтов (`pcaps`/`pproto`) до появления первого протокол-engine — митигация: крейты-заглушки, реальные типы появляются в TICKET-10/11/12, не здесь.

## Implementation notes

Структура: `crates/` (девять крейтов-заглушек), `app/` (Tauri 2 + React 19 + Vite 6), `tools/{emu,protodoc}` (члены workspace), `tools/ingest` (отдельный workspace), `data/{devices,layouts,protocols}`, `scripts/`, `.github/workflows/ci.yml`, `deny.toml`, `rust-toolchain.toml`, `.gitattributes`, `README.md`.

**Что сделано сверх «пустых заглушек» и почему.** Тикет требует заглушки, а не реализацию, и кода бизнес-логики нет. Но три вещи объявлены как типы/контракты, потому что тикет сам это требует в разделе Expected architecture impact, и потому что следующий тикет иначе будет писать код против голого `hidapi`:

1. `ptransport`: `DeviceId`, `SessionHandle`, `TransportError` с явным вариантом `EndpointStalled` (не сопоставление текста чужой ошибки — см. TICKET-21, метод 4) и `AccessDenied` (на Linux это почти всегда отсутствующее udev-правило, и это не деталь лога, а отдельное состояние).
2. `app`: полный IPC-контракт из трёх механизмов — commands (`build_id`, `list_devices`), events (константы имён + типы payload `ProtocolErrorEvent`/`BatteryEvent`), channels (`AnalogSample` + команда `subscribe_analog_stream`, которая **зарегистрирована и явно возвращает ошибку «не реализовано»**, чтобы контракт был настоящим, а не декоративным). Зеркальная сторона на TypeScript — `app/src/ipc.ts`.
3. `app`: single-instance plugin. Это правка спецификации из TICKET-21 (две копии приложения заклинивают endpoint), и её дешевле заложить сейчас, чем добавлять к готовому UI.

**Автоматизация вместо ручной проверки.** Acceptance criterion про направление DAG говорил «проверяется вручную по Cargo.toml». Заменено на `scripts/check_crate_dag.py` + отдельную CI-джобу: проверяются направление рёбер (строго вниз по слоям), листовость `ptransport`/`pcaps`, отсутствие крейта без назначенного слоя и отсутствие `tools/ingest` среди членов workspace. Ручная проверка такого инварианта разъезжается — это ровно тот класс инвариантов, который у prior art дважды разъехался (TICKET-21, метод 10).

**Изоляция `tools/ingest` проверяется тремя независимыми способами**, потому что комментарий в манифесте — не защита: (а) `cargo metadata` не знает про `pingest`; (б) сборка workspace не производит артефакт `pingest*`; (в) сам крейт при этом обязан компилироваться отдельно — исключённый крейт, который тихо сгнил, изолирован не в том смысле.

**Найдено при проверке лицензий:** `hidapi` 2.6.6 резолвится как чистый `MIT`, а не как мульти-лицензия. Заготовленное исключение в `deny.toml` убрано — исключение заменяет allow-list для крейта, то есть создаёт ровно ту дыру, ради закрытия которой файл существует. Факт и его дата зафиксированы комментарием.

**Свой же lint поймал дефект.** `clippy::expect_used` (включён как `warn` на весь workspace, в CI поднимается до ошибки) отклонил `.expect()` в старте Tauri. Исправлено не подавлением lint'а, а по существу: ошибка старта логируется, печатает одну строку в stderr и завершает процесс с кодом 1. Panic здесь напечатал бы backtrace в консоль, которой у пользователя нет, и окна для показа ошибки ещё не существует.

**Мелочи, зафиксированные сразу:** `build.rs` вкладывает короткий хеш коммита с пометкой `-dirty` и только из собственного `.git` (TICKET-21, метод 12) — для будущей кнопки «отправить профиль устройства»; `.gitattributes` с `eol=lf`, иначе работа на Windows превращает каждый файл в полный diff на Linux; мобильные наборы иконок, сгенерированные `tauri icon`, удалены — продукт десктопный.

## Deviations

1. **CI не исполнялся.**
   - **Planned:** «`cargo build --workspace` проходит на всех трёх ОС в CI», «CI green на всех трёх ОС».
   - **Actual:** workflow написан и полон (матрица Ubuntu/Windows/macOS, системные зависимости Tauri для Ubuntu, fmt/clippy/test/build, отдельные джобы для лицензий, изоляции `ingest` и DAG), но ни разу не запускался: remote-репозитория не существует, тикет требует только `git init`.
   - **Reason:** создание удалённого репозитория (GitHub) — внешнее действие, публикующее код проприетарного проекта, и оно не входит в scope тикета. Без явного решения пользователя не выполняется.
   - **Consequence:** уверенность в кроссплатформенности — из конфигурации, не из прогона. Реальные риски конкретно здесь: сборка `hidapi` на Ubuntu требует `libudev-dev` (в workflow добавлен) и Tauri-зависимости webkit2gtk (добавлены); на macOS ничего специфичного не требуется. Всё это — предположения до первого прогона.
   - **Follow-up:** первый реальный прогон CI — при создании remote-репозитория; до тех пор ни один тикет не должен считать «CI green» доказанным.

2. **Автоматизация проверки DAG вместо ручной.** Планировалась ручная сверка `Cargo.toml`; сделан скрипт + CI-джоба. Усиление, не ослабление; ручная проверка при этом тоже выполнена при написании манифестов.

3. **Дополнительные файлы вне явного scope:** `.gitattributes`, `README.md`, `rust-toolchain.toml`, `app/src-tauri/icons/*` (иконки обязательны — без `icon.ico` сборка на Windows падает), `scripts/check_crate_dag.py`. Все — инфраструктура, необходимая для выполнения перечисленных критериев или прямо следующая из них.

4. **Установка тулчейна на машину пользователя.** Выполнена с явного разрешения пользователя (rustup 1.29.0 + stable-msvc 1.97.1, VS 2022 Build Tools с workload VCTools). Первая попытка установки Build Tools упала (exit 5008) из-за параллельно шедшей установки rustup через тот же winget; повтор по одному прошёл. Зафиксировано, потому что при воспроизведении на чистой машине это сэкономит диагностику.

## Review findings

Самопроверка против acceptance criteria, `spec.md` и рисков architecture review:

- **Направление зависимостей (риск №5 review).** Автоматическая проверка проходит: 12 крейтов, рёбра строго вниз. **PASS**
- **Утечка типов из `ptransport` (риск №1).** Публичная поверхность крейта — `DeviceId`, `SessionHandle`, `TransportError`; `hidapi::HidDevice` не появляется ни в одной сигнатуре. `hidapi::HidError` протекает как источник в `#[from]` — сознательно: это тип ошибки, не handle, и он не даёт доступа к устройству. Отмечено как точка внимания для TICKET-08. **PASS с замечанием**
- **Обход write-пути (риск №2).** Write-путей пока нет; `psafety` объявлен как единственный путь в документации крейта. Проверять будет нечего до TICKET-11. **N/A**
- **Три механизма IPC (REQUIRED_BEFORE_IMPLEMENTATION).** Заложены все три, включая зарегистрированную channel-команду. **PASS**
- **Изоляция `tools/ingest` (REQUIRED_BEFORE_IMPLEMENTATION).** Три независимых проверки. **PASS**
- **`DeviceSession`/worker (REQUIRED_BEFORE_IMPLEMENTATION).** Публичный контракт объявлен; фактическая worker-модель — TICKET-08, как и предусмотрено тикетом. **PASS в части, отнесённой к этому тикету**
- **Преждевременная фиксация форм крейтов (Risks тикета).** Соблюдено: типы объявлены только там, где тикет этого прямо требует; `pcaps`/`pproto` не содержат ни одного типа. **PASS**
- **Скоуп не расширен в бизнес-логику.** Ни одной строки протокольной, реестровой или транспортной логики. **PASS**

Блокирующих findings нет. Одно замечание (`hidapi::HidError` в публичном типе ошибки) передано в TICKET-08 как точка внимания, не как блокер.

## Completion evidence

Все команды выполнены локально на Windows 11, Rust 1.97.1 (stable-msvc), Node 24.15.0.

| Команда | Результат |
|---|---|
| `cargo build --workspace` | Finished dev profile, 0 ошибок |
| `cargo test --workspace` | ok; 1 passed (build_id), 22 пустых набора |
| `cargo fmt --all --check` | OK |
| `cargo clippy --workspace --all-targets -- -D warnings` | OK (после исправления `expect_used`) |
| `cargo deny --all-features check licenses` | exit 0 |
| `cargo deny --all-features check bans sources` | exit 0 (дубликаты версий — warn по конфигурации) |
| `python scripts/check_crate_dag.py` | «Crate DAG OK: 12 crates, dependencies point one way» |
| `cargo metadata` ∌ `pingest` | OK |
| `cargo check` в `tools/ingest` | OK (компилируется отдельно) |
| поиск `pingest*` в `target/` | ничего не найдено |
| `npm run typecheck` | OK (после исправления project reference) |
| `npx vite build` | OK, 33 модуля |
| `npx tauri dev` | процесс `peripheral-app`, окно «Peripheral» |
| повторный запуск `.exe` | число процессов не изменилось — single-instance работает |

Commit: `f770750` — «feat: workspace skeleton, safety boundaries and CI [TICKET-07]». Включает также артефакты TICKET-01/03/04/21, у которых на момент их выполнения ещё не было репозитория.

Не проверено: прогон CI на трёх ОС (см. Deviations 1).
