# Current handoff

Обновлено: 2026-08-17.

## Original goal

Локальный кроссплатформенный конфигуратор для Hall-Effect периферии (клавиатуры в первую очередь), HE-first в маркетинге и universal в архитектуре (`peripheral-core`). Проприетарное коммерческое приложение, полностью офлайн, без аккаунта. Полная формулировка — `spec.md` § Problem / Desired outcome.

## Canonical artifacts

| Файл | Что это |
|---|---|
| `.scratch/peripheral-configurator/spec.md` | каноническая спецификация (FR1–FR12, доменные правила, открытые вопросы) |
| `.scratch/peripheral-configurator/MASTER_PLAN.md` | состояние workflow, разбивка на исполнительные фазы, обзор 20 тикетов, решения |
| `.scratch/peripheral-configurator/EXECUTION_LOG.md` | хронология: интервью, правки плана, записи по завершённым тикетам |
| `.scratch/peripheral-configurator/architecture/INITIAL_REVIEW.md` | архитектурное ревью, crate-DAG (§9), findings уровня REQUIRED_BEFORE_IMPLEMENTATION |
| `.scratch/peripheral-configurator/issues/01..20-*.md` | тикеты |
| `docs/decisions/0001-license.md` | ADR лицензии (proprietary, permissive-only зависимости) |
| `docs/prior-art/inventory.md` | карта prior art: лицензия + режим использования (TICKET-03) |
| `docs/prior-art/royuan.md` | конспект протокола ROYUAN (TICKET-01) |

## Current workflow state

IMPLEMENTING_TICKET → фаза 1 частично закрыта, упёрлась в отсутствие Rust-тулчейна.

## Completed tickets

| Тикет | Статус | Артефакт |
|---|---|---|
| TICKET-04 | DONE (во время планирования) | `LICENSE`, `docs/decisions/0001-license.md` |
| TICKET-03 | DONE | `docs/prior-art/inventory.md` |
| TICKET-01 | DONE_WITH_DEVIATIONS | `docs/prior-art/royuan.md` |

## Active ticket

Нет активного. TICKET-07 выбран следующим, но BLOCKED (см. Known failures or blockers).

## Next eligible ticket

1. **TICKET-07** (Cargo workspace + Tauri skeleton + CI + `git init`) — как только решён вопрос с Rust-тулчейном. Разблокирует TICKET-08/10/11/13, то есть всю фазу 3.
2. **TICKET-02** (письмо автору sharkfin) — черновик может подготовить агент; отправка требует явного разрешения пользователя.
3. **TICKET-05** (OEM-карта топ-моделей) — стал READY после закрытия 01/03; относится к фазе 2, можно взять, если фаза 1 стоит.

## Decisions that must be preserved

Решения пользователя (2026-08-17, интервью — не переспрашивать):

1. Лицензия — proprietary/commercial; GPL-проекты только как prior art (факты), не код.
2. Sharkfin — независимая разработка + контакт с автором ради обмена **информацией**, не кодом/лицензией.
3. Позиционирование — HE-first в маркетинге, universal в архитектуре.
4. Референсное железо — AULA Hero 84 HE (в наличии, primary, **не ROYUAN**) + ROYUAN-плата (к покупке). Оба трека с начала фазы 1.

Архитектурные решения (не пересматривать без ADR): `hidapi` как основная транспортная абстракция; физический HID-handle владеется исключительно `DeviceSession` в выделенном worker-потоке; IPC — три механизма Tauri (commands/events/channels), Channels обязательны для `he.analog_stream`; крейты образуют однонаправленный DAG (`INITIAL_REVIEW.md` §9).

Жёсткие запреты: никакой прошивки (flash) в v1; `tools/ingest` физически вне релизной сборки; запись только через `SafeCommandId`; `confidence < Verified` → read-only.

Новое от 2026-08-17 (итог фазы 1): **отсутствие ответа устройства — не доказательство отсутствия возможности**; `Unsupported` требует того же доказательства, что `Verified(hw)`.

## Deviations that affect later work

- TICKET-01: исходники sharkfin не читались (только `PROTOCOL.md`) → таблица VID:PID по 949 платам **не** перенесена, её берёт TICKET-05, и берёт как данные с проверкой лицензии на данные.
- TICKET-01: в `spec.md` добавлено additive доменное правило (см. выше). Тикеты не переупорядочивались.
- TICKET-04 выполнен вне последовательности Phase 8 (во время планирования).
- Скилл `ticket-autopilot` ссылается на не установленные дочерние скиллы (`implement`, `to-spec`, `to-tickets`, `handoff`, `grill-with-docs`…). Работа ведётся по шаблонам скилла вручную; `/code-review` для документационных тикетов заменён самопроверкой против acceptance criteria (кода и diff'а нет).

## Current repository state

- **Git-репозитория ещё нет** (`git init` входит в scope TICKET-07). Все артефакты — просто файлы на диске.
- Продуктового кода нет ни строки. Существуют: `LICENSE`, `docs/decisions/0001-license.md`, `docs/prior-art/inventory.md`, `docs/prior-art/royuan.md`, `.claude/`, `.scratch/peripheral-configurator/**`.
- Среда: Node.js 24.15.0, npm 11.12.1, git 2.55.0.windows.3, Windows 11 Pro. **Rust-тулчейна нет.** `cargo-deny`, Tauri CLI — тоже нет (следствие).

## Relevant commits

Нет — репозиторий не инициализирован. Первый коммит создаётся в TICKET-07 и должен включить артефакты TICKET-04/03/01.

## Verification already performed

- Лицензии 15 prior-art проектов — проверены по страницам репозиториев 2026-08-17; неподтверждённое помечено как неподтверждённое.
- Источник протокольных фактов запинен: sharkfin `master` @ `d657f199a82a46c45ab5d1327d88e843689ce6a9` (v0.3.5).
- Наличие тулчейна проверено дважды (bash PATH и PowerShell `Get-Command`, плюс отсутствие `~/.cargo/bin`).
- Автоматических проверок не запускалось — запускать пока нечего.

## Known failures or blockers

**BLOCKER (требует решения пользователя): нет Rust-тулчейна.** TICKET-07 требует `cargo build --workspace`, `cargo-deny check licenses`, `cargo tauri dev`. Для Windows нужен rustup + MSVC build tools (Tauri на Windows требует MSVC-таргет и WebView2, который на Windows 11 предустановлен). Установка меняет машину пользователя и не входит в scope тикета → без явного разрешения не выполняется.

Возможные пути (решает пользователь):
1. разрешить установку rustup + MSVC build tools на этой машине;
2. отложить TICKET-07 и вести фазу 2 (TICKET-05, затем TICKET-06 — покупка платы), вернувшись к скелету позже;
3. подготовить весь скелет workspace как файлы **без сборки** (Cargo.toml/lib.rs/CI-конфиги пишутся, `cargo build` не запускается) — тогда acceptance criteria TICKET-07 останутся невыполненными (CI green на трёх ОС, локальный запуск Tauri), и тикет закроется только после появления тулчейна. Это осознанно частичное выполнение, а не DONE.

## Files most relevant to the next ticket (TICKET-07)

- `issues/07-workspace-skeleton.md` — scope и acceptance criteria;
- `architecture/INITIAL_REVIEW.md` §6 и §9 — три из четырёх findings REQUIRED_BEFORE_IMPLEMENTATION закрываются именно этим тикетом, плюс диаграмма crate-DAG;
- `spec.md` FR7 (транспорт на `hidapi`), FR9 (`tools/ingest` вне релиза), FR10 (`DeviceSession`), FR11 (три механизма IPC);
- `docs/decisions/0001-license.md` — deny-list для `cargo-deny`.

## Exact recommended next action

Получить от пользователя решение по блокеру Rust-тулчейна (три варианта выше). После разрешения — TICKET-07 в порядке: `git init` → workspace `Cargo.toml` с направлением path-зависимостей по DAG §9 → крейты-заглушки (`ptransport` с `hidapi` и пустым `platform::{windows,linux,macos}`, публичные типы `DeviceId`/`SessionHandle` без утечки handle) → `tools/ingest` отдельным workspace вне `[workspace.members]` → `app/` (Tauri + React, три механизма IPC заложены) → `deny.toml` с GPL/AGPL/LGPL в deny → CI на трёх ОС → первый коммит, включающий артефакты TICKET-04/03/01.
