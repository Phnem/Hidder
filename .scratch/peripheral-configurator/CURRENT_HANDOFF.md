# Current handoff

Обновлено: 2026-08-17, после закрытия фазы 1.

## Original goal

Локальный кроссплатформенный конфигуратор для Hall-Effect периферии (клавиатуры в первую очередь), HE-first в маркетинге и universal в архитектуре (`peripheral-core`). Проприетарное коммерческое приложение, полностью офлайн, без аккаунта. Полная формулировка — `spec.md`.

## Canonical artifacts

| Файл | Что это |
|---|---|
| `.scratch/peripheral-configurator/spec.md` | каноническая спецификация (FR1–FR12, доменные правила, открытые вопросы) |
| `.scratch/peripheral-configurator/MASTER_PLAN.md` | состояние workflow, разбивка на 6 исполнительных фаз, обзор 21 тикета, решения, команды верификации |
| `.scratch/peripheral-configurator/EXECUTION_LOG.md` | хронология: интервью, правки плана, записи по каждому завершённому тикету |
| `.scratch/peripheral-configurator/architecture/INITIAL_REVIEW.md` | архитектурное ревью, crate-DAG (§9), findings |
| `.scratch/peripheral-configurator/issues/01..21-*.md` | тикеты |
| `docs/decisions/0001-license.md` | ADR лицензии |
| `docs/prior-art/inventory.md` | карта prior art: лицензия + режим использования (TICKET-03) |
| `docs/prior-art/royuan.md` | конспект протокола ROYUAN (TICKET-01) |
| `docs/prior-art/sharkfin-methods.md` | инженерные методы sharkfin (TICKET-21) |
| `README.md` | что это, структура репозитория, необсуждаемые правила, команды |

## Current workflow state

READY_FOR_IMPLEMENTATION. Фаза 1 закрыта.

## Completed tickets

| Тикет | Статус | Артефакт |
|---|---|---|
| TICKET-04 | DONE | `LICENSE`, `docs/decisions/0001-license.md` |
| TICKET-03 | DONE | `docs/prior-art/inventory.md` |
| TICKET-01 | DONE_WITH_DEVIATIONS | `docs/prior-art/royuan.md` |
| TICKET-21 | DONE | `docs/prior-art/sharkfin-methods.md` |
| TICKET-07 | DONE_WITH_DEVIATIONS | коммит `f770750` — весь скелет |

## Active ticket

Нет.

## Next eligible ticket

1. **TICKET-08** — Windows HID inventory на AULA Hero 84 HE. Плата в наличии, блокеров нет, разблокирует TICKET-10/12 и закрывает открытый вопрос Q2. Рекомендуемый следующий шаг.
2. **TICKET-11** — `psafety` ACL + `SafeCommandId` skeleton. READY, не требует железа.
3. **TICKET-05** — OEM-карта топ-моделей (фаза 2), READY.
4. **TICKET-02** — письмо автору sharkfin; отложено пользователем, требует его действия.

## Decisions that must be preserved

Решения пользователя (не переспрашивать):

1. Лицензия — proprietary; GPL-проекты только как источник фактов и методов, не кода.
2. Sharkfin — независимая разработка; контакт с автором ради обмена информацией. Изучение его исходников разрешено пользователем явно (2026-08-17) и не отменяет ADR-0001.
3. Позиционирование — HE-first в маркетинге, universal в архитектуре.
4. Референсное железо — AULA Hero 84 HE (в наличии, primary, **не ROYUAN**) + ROYUAN-плата (к покупке).
5. Установка Rust-тулчейна на машину — разрешена и выполнена.

Архитектурные решения: `hidapi` как основная транспортная абстракция; handle владеется исключительно `DeviceSession` в выделенном worker-потоке; IPC — три механизма Tauri, Channels обязательны для аналогового стрима; крейты образуют однонаправленный DAG.

Жёсткие запреты: никакой прошивки в v1; `tools/ingest` вне релизной сборки; запись только через `SafeCommandId`; `confidence < Verified` → read-only; никогда не показывать контрол без подтверждённой команды.

Доменные правила, добавленные в фазе 1: отсутствие ответа устройства — не доказательство отсутствия возможности; настройка, переживающая выключение питания, — это flash-запись, и её класс задаёт интервал до и тишину после; второй экземпляр приложения не запускается; stall останавливает и фоновый опрос.

## Deviations that affect later work

- **CI ни разу не исполнялся** — нет remote-репозитория. Ни один тикет не вправе считать «CI green» доказанным. Первый прогон проверит два конкретных предположения: `libudev-dev` для `hidapi` на Ubuntu и набор webkit2gtk-зависимостей Tauri.
- TICKET-01: исходники sharkfin по протоколу не читались построчно; таблица VID:PID по 949 платам — задача TICKET-05, брать как **данные** с проверкой лицензии на данные.
- TICKET-21 заведён вместо действия TICKET-02; TICKET-02 не отменён.
- Скилл `ticket-autopilot` ссылается на не установленные дочерние скиллы; работа ведётся по его шаблонам вручную, `/code-review` для документационных тикетов заменён самопроверкой.

## Current repository state

- Git-репозиторий инициализирован, ветка `main`, один коммит `f770750`, remote отсутствует.
- Рабочее дерево чистое, кроме обновлений артефактов планирования этой сессии (тикеты/лог/handoff/master plan).
- `sharkfin/` — локальная копия чужого GPL-проекта, в `.gitignore`, коммититься не должна никогда.
- Продуктового кода нет: все крейты — заглушки с документацией. Единственный тест — `build_id` в `app`.

## Relevant commits

`f770750` — «feat: workspace skeleton, safety boundaries and CI [TICKET-07]». Включает артефакты TICKET-01/03/04/21.

## Verification already performed

Локально на Windows 11 (Rust 1.97.1 stable-msvc, Node 24.15.0): `cargo build/test/fmt/clippy -D warnings`, `cargo deny check licenses` и `bans sources`, `scripts/check_crate_dag.py`, три проверки изоляции `tools/ingest`, `npm run typecheck`, `npx vite build`, запуск `npx tauri dev` (окно «Peripheral»), проверка single-instance. Полная таблица — `issues/07-*.md`.

## Known failures or blockers

Блокеров нет. Два открытых пункта, требующих решения пользователя, но не блокирующих работу:

1. Создание remote-репозитория (для реального прогона CI) — публикация кода проприетарного проекта, решает пользователь.
2. Покупка ROYUAN-платы (TICKET-06) — блокирует только ROYUAN-трек (TICKET-09), не AULA-трек.

## Files most relevant to the next ticket (TICKET-08)

- `issues/08-windows-hid-inventory-aula.md` — scope и acceptance criteria;
- `crates/ptransport/src/lib.rs` — контракт, против которого надо писать с первой строки: `DeviceId`/`SessionHandle`/`TransportError`, и правило, что handle не покидает крейт;
- `crates/ptransport/src/platform/windows.rs` — пустой escape hatch; TICKET-08 решает, нужен ли он вообще;
- `docs/prior-art/royuan.md` — конкретные вопросы к эмпирической проверке (одна ли vendor-коллекция у конфигурационного канала и аналогового стрима);
- `spec.md` AC3 и открытый вопрос Q2 — то, что этот тикет обязан закрыть данными.

## Exact recommended next action

TICKET-08: подключить AULA Hero 84 HE и снять полный HID-инвентарь на Windows через `hidapi` — какие top-level коллекции перечисляются, какие открываются, какие дают access denied, есть ли vendor-defined коллекция с аналоговым стримом. Писать код сразу против `DeviceSession`/`SessionHandle`, а не против `hidapi::HidDevice`: разведочный код, написанный напрямую поверх `hidapi`, протащит предположения о владении handle во всё, что его вызовет.

Одно замечание из ревью TICKET-07 к этому тикету: `hidapi::HidError` сейчас протекает в `TransportError` как источник `#[from]`. Это тип ошибки, а не handle, но при добавлении реального I/O проверить, что вместе с ним не начали протекать и другие типы `hidapi`.
