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
| TICKET-08 | DONE_WITH_DEVIATIONS | `ptransport::inventory` + `docs/hardware/aula-hero-84-he.{json,md}` |

### Что известно про AULA после инвентаря

VID `0x372E`, PID `0x103E`, строки `BY Tech` / `HERO 84 HE` (бренд AULA в строках устройства не фигурирует вообще). 7 top-level collections на 3 USB-интерфейсах, все открылись без прав администратора. **Два** vendor-TLC: `0xFF00:0x0001` (только feature-репорты, 64 байта) и `0xFF60:0x0061` (in/out по 64 байта). Какой из них конфигурационный и есть ли в них аналоговый поток — неизвестно, требует I/O.

Плата **не ROYUAN**: VID отсутствует в списке семейства, форма vendor-канала другая. Теперь это факт, а не предположение.

## Active ticket

Нет.

## Next eligible ticket

Порядок работ фазы 2 (перестроена 2026-08-17):

1. ~~**TICKET-08** — HID inventory AULA Hero 84 HE~~ — **выполнен**.
2. **TICKET-06** — точная идентификация EPOMAKER (модель, HE или механика, тип подключения, конфигуратор, признаки OEM). ← следующий
3. **TICKET-09** — HID inventory EPOMAKER **тем же `ptransport`** + сравнительный отчёт двух устройств.
4. **TICKET-05** — карта платформ; идёт параллельно.
5. **TICKET-11** — `psafety` skeleton; железа не требует, идёт параллельно.
6. **TICKET-02** — письмо автору sharkfin; отложено пользователем.

## Decisions that must be preserved

Решения пользователя (не переспрашивать):

1. Лицензия — proprietary; GPL-проекты только как источник фактов и методов, не кода.
2. Sharkfin — независимая разработка; контакт с автором ради обмена информацией. Изучение его исходников разрешено пользователем явно (2026-08-17) и не отменяет ADR-0001.
3. Позиционирование — HE-first в маркетинге, universal в архитектуре.
4. Референсное железо — AULA Hero 84 HE (primary, **не ROYUAN**) + полноразмерная EPOMAKER (secondary). **Обновлено 2026-08-17: ROYUAN-платы не будет, покупка отменена.** Принадлежность EPOMAKER к какому-либо protocol family **не предполагается** — её устанавливает фаза 2 по данным, и вывод по бренду прямо запрещён.
5. Установка Rust-тулчейна на машину — разрешена и выполнена.
6. Фаза 2 перестроена вокруг двух имеющихся устройств; фаза 5 переименована в «Second protocol family» — какое семейство станет вторым, решают данные, а не план.

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

Блокеров нет; оба референсных устройства в наличии.

Один открытый пункт, требующий решения пользователя и не блокирующий работу: создание remote-репозитория (для реального прогона CI) — публикация кода проприетарного проекта.

Один остаточный риск, который перестройкой фазы 2 **не закрыт**: EPOMAKER может оказаться механической, родственной AULA или без доступного config-интерфейса, и тогда второго HE-семейства не появится. `ProtocolEngine` рискует дойти до фазы 4 провалидированным на одном семействе (риск №3 architecture review). Признан осознанно; решение о втором engine принимается по данным фазы 2.

## Files most relevant to the next ticket (TICKET-06)

- `issues/06-identify-epomaker-board.md` — scope, форма карточки, запрет выводов по бренду;
- `docs/hardware/README.md` — формат инвентаря и обязательная проверка полноты через `Get-PnpDevice`; TICKET-09 обязан снять данные тем же способом;
- `docs/hardware/aula-hero-84-he.{json,md}` — эталон, с которым будет сравниваться EPOMAKER;
- `crates/ptransport/examples/inventory.rs` — инструмент; для EPOMAKER меняется только `--device`, `--label` и `--out`;
- `spec.md` Q3 — если EPOMAKER окажется беспроводной, wireless/battery-трек перестаёт быть заблокированным до фазы 6.

## Exact recommended next action

TICKET-06: определить, что за EPOMAKER в руках — точная модель и ревизия, HE или обычная механика, USB/2.4/BT, официальный конфигуратор и на чём он сделан, заявленные функции, признаки известного OEM. Ничего не выводить из бренда. Результат — карточка в `docs/prior-art/epomaker.md`.

Полезная подсказка из TICKET-08: обзорный режим инструмента (`cargo run -p ptransport --example inventory` без аргументов) сразу покажет VID:PID и строки производителя подключённого устройства, и уже это часто опровергает предположение по бренду — у AULA строки оказались `BY Tech`, слова AULA в устройстве нет вовсе.

Предупреждение для TICKET-09: `opened = true` в инвентаре означает только доступ уровня перечисления. Не читать это как «с устройством можно обмениваться».
