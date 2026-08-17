# Current handoff

Обновлено: 2026-08-18, после закрытия TICKET-11.

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

READY_FOR_IMPLEMENTATION. Фазы 1 и 2 закрыты; идёт Phase 3 (read-only вертикальный срез).

## Completed tickets

| Тикет | Статус | Артефакт |
|---|---|---|
| TICKET-04 | DONE | `LICENSE`, `docs/decisions/0001-license.md` |
| TICKET-03 | DONE | `docs/prior-art/inventory.md` |
| TICKET-01 | DONE_WITH_DEVIATIONS | `docs/prior-art/royuan.md` |
| TICKET-21 | DONE | `docs/prior-art/sharkfin-methods.md` |
| TICKET-07 | DONE_WITH_DEVIATIONS | коммит `f770750` — весь скелет |
| TICKET-08 | DONE_WITH_DEVIATIONS | `ptransport::inventory` + `docs/hardware/aula-hero-84-he.{json,md}` |
| TICKET-11 | DONE_WITH_DEVIATIONS | `psafety` целиком + `data/protocols/*.toml` |

### Что теперь невозможно обойти (TICKET-11)

- Опкод, помеченный `destructive`/`unknown` или не помеченный никем, **не имеет представления в программе**: варианта в `SafeCommandId` нет, а построить его из байта нечем — публичного пути «байт → команда» не существует.
- `SafeCommandId::opcode()` — `pub(crate)`. Байт читается ровно в одном месте: когда гейт выпускает `AuthorizedCommand` после всех проверок.
- `AuthorizedCommand` невозможно сконструировать снаружи и невозможно переиграть (не `Clone`, потребляется при dispatch).
- Гейт **владеет** sink'ом; у engine нет объекта, через который можно отправить что-то мимо. Batch-входа нет — одна команда за вызов.
- ACL keyed by family; команда чужого семейства отклоняется (`Refusal::WrongFamily`).
- Write-класс без записанного backup отклоняется.
- Тайминги только измеренные, из ACL-файла семейства; глобальных констант нет. Неизмеренное семейство → подтверждение на **каждую** операцию, `UserConfirmation` принимается по значению.
- Stall → карантин устройства, включая чтения; снимается только `device_reconnected()`. Ретраев нет нигде.

Состояние реестра: **16 команд, все read/probe, write-команд ноль. У `aula-hero84-he` — ноль команд вообще.**

### Что известно про AULA после инвентаря

VID `0x372E`, PID `0x103E`, строки `BY Tech` / `HERO 84 HE` (бренд AULA в строках устройства не фигурирует вообще). 7 top-level collections на 3 USB-интерфейсах, все открылись без прав администратора. **Два** vendor-TLC: `0xFF00:0x0001` (только feature-репорты, 64 байта) и `0xFF60:0x0061` (in/out по 64 байта). Какой из них конфигурационный и есть ли в них аналоговый поток — неизвестно, требует I/O.

Плата **не ROYUAN**: VID отсутствует в списке семейства, форма vendor-канала другая. Теперь это факт, а не предположение.

## Изменение плана 2026-08-17 (после TICKET-08): где теперь EPOMAKER

**EPOMAKER физически находится не у разработчика, а у стороннего владельца (отца пользователя).** Прогонять по ней developer-инструменты нельзя: установка Rust/cargo/Build Tools/USBPcap/скриптов на чужой компьютер противоречит цели продукта и не даёт product-level валидации.

Поэтому:

- **EPOMAKER больше не reference board.** Единственная reference board проекта — AULA Hero 84 HE.
- Новая роль EPOMAKER — **remote external validation device**: поздняя проверка более-менее готовой пользовательской сборки на неизвестном устройстве у постороннего человека. Она проверит сразу generic `ptransport`, `pregistry`/fingerprinting, безопасный Inventory/Export flow, редактирование серийника, поведение на неизвестном устройстве, отсутствие AULA-специфичных предположений, будущий Learning Mode Level 0 и удобство сценария без dev-toolchain.
- **TICKET-06 и TICKET-09** переведены в состояние `DEFERRED_REMOTE_VALIDATION` (не отменены). Порядок между ними развернулся: сначала **09** (артефакт от приложения), затем **06** (идентификация по артефакту + публичным источникам).
- **Гейт возврата — capability-based, не «после фазы N»:** нужна обычная Windows-сборка, которая безопасно перечисляет неизвестное устройство, ничего в него не пишет, генерирует versioned inventory-артефакт, сама редактирует серийник и показывает одну кнопку **Export**. Естественно — после TICKET-13/14 или как ранний безопасный Level 0 из TICKET-16, и не раньше, чем UX перестанет выглядеть как инженерный debug tool.
- **Заранее ничего не назначать:** ни второе protocol family, ни HE, ни ROYUAN, ни конкретный OEM, ни wireless. Только по данным.
- **`if epomaker { ... }` в транспорте запрещён.** Не справился generic `ptransport` — чинится абстракция, а не создаётся vendor-specific enumeration.

Это не ослабление плана: EPOMAKER стала более ценным тестом — репетицией community-submission на живом человеке.

## Active ticket

Нет.

## Next eligible ticket

Основной code path (закреплён 2026-08-17): **11 → 10 → 12 → 13 → 14 → 15 → 16**.

1. ~~**TICKET-11** — `psafety` ACL + `SafeCommandId`~~ — **выполнен**.
2. **TICKET-10** — `pregistry` + многосигнальный fingerprinting, семя — реальные данные AULA. ← **следующий**
3. **TICKET-12** — первый AULA engine, строго read-only, через `DeviceSession`/`pregistry`/capability-модель/safety-границы, не через `hidapi` напрямую.
4. **TICKET-13** → **TICKET-14** — UI и эмулятор.
5. **TICKET-05** — OEM-карта; параллельный research track, ничего не блокирует.
6. **TICKET-09 → TICKET-06** — remote-валидация EPOMAKER; параллельный трек за capability-гейтом.
7. **TICKET-02** — письмо автору sharkfin; отложено пользователем.

## Decisions that must be preserved

Решения пользователя (не переспрашивать):

1. Лицензия — proprietary; GPL-проекты только как источник фактов и методов, не кода.
2. Sharkfin — независимая разработка; контакт с автором ради обмена информацией. Изучение его исходников разрешено пользователем явно (2026-08-17) и не отменяет ADR-0001.
3. Позиционирование — HE-first в маркетинге, universal в архитектуре.
4. Референсное железо — **AULA Hero 84 HE, единственная плата у разработчика** (не ROYUAN: покупка отменена 2026-08-17; не EPOMAKER: она у стороннего владельца). Принадлежность EPOMAKER к какому-либо protocol family **не предполагается** — её устанавливают данные удалённого инвентаря, и вывод по бренду прямо запрещён.
5. Установка Rust-тулчейна на машину разработчика — разрешена и выполнена. **На чужом компьютере не устанавливается ничего, кроме обычной сборки Peripheral.**
6. Phase 2 сведена к выполненному TICKET-08; TICKET-05 — параллельный research track; TICKET-06/09 — remote-validation track; фаза 5 называется «Second protocol family» — какое семейство станет вторым, решают данные, а не план.
7. Порядок основного code path: TICKET-11 → 10 → 12 → 13 → 14 → 15 → 16. Safety-граница раньше первого engine.

Архитектурные решения: `hidapi` как основная транспортная абстракция; handle владеется исключительно `DeviceSession` в выделенном worker-потоке; IPC — три механизма Tauri, Channels обязательны для аналогового стрима; крейты образуют однонаправленный DAG.

Отложено в архитектурный чекпоинт (не править сейчас, но и не потерять): формулировка FR10 (`DeviceSession` владеет **набором** handle'ов — `CollectionHandleSet`; инвариант «handles не покидают `ptransport`» сохраняется), нужна ли platform-native классификация ошибок вместо текстовых `hidapi`, различие «enumeration access ≠ usable read/write channel». Повестка — в `MASTER_PLAN.md`.

Жёсткие запреты: никакой прошивки в v1; `tools/ingest` вне релизной сборки; запись только через `SafeCommandId`; `confidence < Verified` → read-only; никогда не показывать контрол без подтверждённой команды.

Доменные правила, добавленные в фазе 1: отсутствие ответа устройства — не доказательство отсутствия возможности; настройка, переживающая выключение питания, — это flash-запись, и её класс задаёт интервал до и тишину после; второй экземпляр приложения не запускается; stall останавливает и фоновый опрос.

Добавлено после TICKET-08: **идентификаторы экземпляра устройства редактируются по умолчанию** — в любой передаваемый артефакт попадает `serial present: true`, а не значение. Политика общая, не AULA-специфичная.

## Deviations that affect later work

- **CI ни разу не исполнялся** — нет remote-репозитория. Ни один тикет не вправе считать «CI green» доказанным. Первый прогон проверит два конкретных предположения: `libudev-dev` для `hidapi` на Ubuntu и набор webkit2gtk-зависимостей Tauri.
- TICKET-01: исходники sharkfin по протоколу не читались построчно; таблица VID:PID по 949 платам — задача TICKET-05, брать как **данные** с проверкой лицензии на данные.
- TICKET-21 заведён вместо действия TICKET-02; TICKET-02 не отменён.
- Скилл `ticket-autopilot` ссылается на не установленные дочерние скиллы; работа ведётся по его шаблонам вручную, `/code-review` для документационных тикетов заменён самопроверкой.

## Current repository state

- Git-репозиторий инициализирован, ветка `main`, remote отсутствует. Последние коммиты: `f770750` (скелет), `503070e` (TICKET-08), плюс planning-коммиты.
- Рабочее дерево чистое, кроме обновлений артефактов планирования этой сессии (тикеты/лог/handoff/master plan).
- `sharkfin/` — локальная копия чужого GPL-проекта, в `.gitignore`, коммититься не должна никогда.
- Продуктового кода два крейта: `ptransport` (inventory, TICKET-08) и `psafety` (границa записи целиком, TICKET-11). Остальные — заглушки с документацией. Тестов в workspace 65.

## Relevant commits

`f770750` — «feat: workspace skeleton, safety boundaries and CI [TICKET-07]». Включает артефакты TICKET-01/03/04/21.

`503070e` — «feat(ptransport): read-only HID inventory, captured on the AULA board [TICKET-08]». `ptransport::inventory` + `examples/inventory.rs` + артефакты `docs/hardware/`.

## Verification already performed

Локально на Windows 11 (Rust 1.97.1 stable-msvc, Node 24.15.0): `cargo build/test/fmt/clippy -D warnings`, `cargo deny check licenses` и `bans sources`, `scripts/check_crate_dag.py`, три проверки изоляции `tools/ingest`, `npm run typecheck`, `npx vite build`, запуск `npx tauri dev` (окно «Peripheral»), проверка single-instance. Полная таблица — `issues/07-*.md`.

## Known failures or blockers

**Блокеров для TICKET-11 → 10 → 12 нет.** Ни один из них не зависит от TICKET-06/09; ни один не ждёт TICKET-05.

Открытые пункты, не блокирующие работу:

- **Последний участок пути к устройству держится на соглашении, а не на типах.** `ptransport` не имеет write-API; когда он появится (TICKET-12/15), он обязан принимать только то, что произвёл `SafetyGate`. Записано в doc-комментарии `psafety` и в риск-регистре, чтобы не выглядело закрытым.
- **Confidence не выражен в гейте.** `identify_device` несёт семейство без confidence; правило «write только при `Verified`» ждёт `pregistry` (TICKET-10).

- Создание remote-репозитория (для реального прогона CI) — требует решения пользователя, публикация кода проприетарного проекта.
- **Второй платы у разработчика нет вообще.** Риск «архитектура валидирована на одном устройстве и одном protocol family» (риск №3 architecture review, «premature trait finalization») **усилился** и остаётся открытым. До первого remote-артефакта единственное «второе устройство» в тестах — эмулятор, собранный из записей самой AULA, а значит не способный опровергнуть AULA-специфичные предположения. Считать этот риск закрытым переносом EPOMAKER **нельзя**; он обязан быть виден на архитектурном ревью.

## Files most relevant to the next ticket (TICKET-10)

- `issues/10-multi-signal-fingerprinting.md` — scope, acceptance criteria, TDD REQUIRED;
- `docs/hardware/aula-hero-84-he.{json,md}` — единственные реальные данные устройства: descriptor topology, 7 TLC, VID/PID, строки, report IDs и размеры, release, два vendor-TLC;
- `spec.md` Приложение B — схема реестра; FR4 — многосигнальный matcher с explicit confidence;
- `spec.md` § Domain rules — «family не выводится из VID:PID», порядок силы сигналов;
- `crates/psafety/src/gate.rs` — `identify_device(device, family)`: **точка, куда TICKET-10 обязан довезти confidence**. Сейчас binding несёт только семейство, и правило «write только при `confidence >= Verified`» в гейте не выражено. Сегодня разрешать нечего (write-команд ноль), но к TICKET-15 это дыра;
- `data/protocols/*.toml` — как выглядит per-family источник, из которого генерируется код; `pregistry` использует ту же схему `opcode_acl` (`spec.md` Приложение B), их надо держать согласованными;
- `docs/prior-art/royuan.md` — сигналы fingerprint (identify `0x8F`, revision `0x80`) уже классифицированы как `probe_ok` в ACL.

## Exact recommended next action

**TICKET-10.** Реализовать `pregistry`: схему из Приложения B, YAML → SQLite build-шаг, многосигнальный matcher (report-descriptor hash → TLC-набор → strings → identify-опкод → fw-версия → VID:PID) с explicit confidence, первая реальная запись устройства — AULA Hero 84 HE из данных TICKET-08.

Что важно не потерять на стыке с TICKET-11:

- confidence обязан доехать до `SafetyGate::identify_device` — иначе к первому write-тикету правило `confidence >= Verified` окажется незаписанным нигде в коде;
- matcher обязан объяснять, какие сигналы совпали, а не возвращать bool;
- второго физического устройства нет, поэтому «различает ли matcher два разных устройства» проверяется синтетикой (одинаковый VID:PID, разный descriptor hash) и эмулятором (TICKET-14), а не железом. Ограничение зафиксировать явно, а не обходить ожиданием.

Предупреждения из TICKET-08, всё ещё релевантные:

- `opened = true` означает только доступ уровня перечисления — не «с устройством можно обмениваться». Не повышать confidence на этом основании.
- Классификация ошибок `hidapi` по тексту не работает. TICKET-11 зафиксировал границу (kill-switch только на типизированном `EndpointStalled`); решение о platform-native классификации — TICKET-12/15.
