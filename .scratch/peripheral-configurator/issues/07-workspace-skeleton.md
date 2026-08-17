# TICKET-07: Cargo workspace + Tauri skeleton + CI

## Status

READY (dependency TICKET-04 is DONE)

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

- [ ] `cargo build --workspace` проходит на всех трёх ОС в CI.
- [ ] `cargo-deny check licenses` настроен и проходит (пустой workspace без запрещённых лицензий).
- [ ] `tools/ingest` физически не собирается при обычном `cargo build --workspace` из корня (проверяется отдельной CI-проверкой, что итоговые артефакты релизной сборки не содержат `ingest`-бинарь/код).
- [ ] Tauri-приложение запускается локально (пустой UI) на минимум одной ОС разработчика.
- [ ] Репозиторий инициализирован как git-репозиторий с первым коммитом.
- [ ] Ни один крейт-заглушка не имеет `path`-зависимости, идущей "вверх" по DAG (architecture review §9) — проверяется вручную по `Cargo.toml` каждого крейта.

## Verification plan

CI green на всех трёх ОС; ручной запуск `cargo tauri dev` (или аналог) для проверки, что приложение стартует.

## TDD classification

NOT_NEEDED (skeleton/infrastructure — нет бизнес-логики для TDD)

## Expected architecture impact

Формирует все дальнейшие границы крейтов. См. `architecture/INITIAL_REVIEW.md` — этот тикет обязан закрыть три из четырёх REQUIRED_BEFORE_IMPLEMENTATION финдингов: (1) изоляция `tools/ingest`; (2) направление crate-DAG (§9) зафиксировано в `Cargo.toml` до появления кода; (3) трёхканальный IPC-контракт (commands/events/channels) заложен в `app/`-скелете. Четвёртый финдинг — `DeviceSession`/worker-модель — закрывается фактически в TICKET-08 (первый реальный код `ptransport`), но публичный контракт (`DeviceId`/`SessionHandle`, не голый handle) должен быть объявлен как типы-заглушки уже здесь, чтобы TICKET-08 с первой строчки писал код против него, а не против голого `hidapi::HidDevice`.

## Risks

- Слишком ранняя фиксация форм крейтов (`pcaps`/`pproto`) до появления первого протокол-engine — митигация: крейты-заглушки, реальные типы появляются в TICKET-10/11/12, не здесь.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
