# TICKET-11: `psafety` — ACL, `SafeCommandId`, rate limiter, journal (skeleton)

## Status

READY — **следующий тикет основного code path** (TICKET-07 выполнен; блокеров нет, железа не требует)

Порядок закреплён 2026-08-17: после TICKET-08 проект впервые приблизился к настоящему I/O, поэтому архитектурная гарантия `raw opcode ✗ → SafeCommandId ✓ → Safety Gate ✓ → Transport` фиксируется **до** появления первого protocol engine. Ни один engine не должен получить возможность случайно обойти safety boundary.

## Objective

Реализовать `psafety`: классификацию опкодов (`safe_read`/`safe_write`/`probe_ok`/`slow_flash`/`destructive`/`unknown`, дефолт `unknown` = запрещено), build-time codegen `SafeCommandId` (closed enum из записей реестра с классом ≠ `destructive`/`unknown`), rate-limiter skeleton (per-family/per-opcode-class), journal skeleton. **До любой записи в устройство** — этот тикет обязан существовать раньше TICKET-12's write-путей (у TICKET-12 в этом тикет-сете есть только read, поэтому формально не блокирует, но по духу спецификации (FR2/FR3) должен быть готов прежде, чем какой-либо будущий write-тикет начнётся).

## User or system value

Это архитектурная гарантия безопасности продукта (spec FR2/FR3, plan §10.6) — единственное, что стоит между кодом и окирпичиванием чужой платы. Не опция, а инвариант.

## Dependencies

TICKET-07; координируется с TICKET-10 по формату `opcode_acl`-таблицы (обе используют одну и ту же схему реестра из `spec.md` Приложение B).

## Scope

- `opcode_acl(protocol_family, opcode, class, note)` — источник для codegen.
- Build-script/codegen: `SafeCommandId` enum, содержащий **только** варианты для opcode с классом `safe_read`/`safe_write`/`probe_ok`/`slow_flash`; `destructive`/`unknown` физически не попадают в enum.
- Compile-time seam test (см. `spec.md` Test seams): тестовый реестр с намеренно "плохой" записью (`destructive`-опкод) — codegen обязан не породить для него `SafeCommandId`-вариант.
- Rate limiter: интерфейс, принимающий family + opcode-class, состояние "endpoint жив/stalled" — для Phase 1 read-only сценария реализация может быть минимальной (нет write-нагрузки ещё), но контракт должен быть готов под будущий `light.per_key`/flash-write сценарий (Phase 2+). Rate limiter не владеет физическим handle и не делает I/O напрямую — он выдаёт разрешение/отказ команде, которая затем идёт через `DeviceSession`'s command queue (TICKET-07/08 contract); "endpoint stalled" — это состояние, о котором `psafety` узнаёт от `ptransport`/сессии, а не то, что оно детектирует само через прямой опрос handle.
- Journal skeleton: запись каждого исполненного `SafeCommandId` (даже read) с таймстампом — основа для будущего Journal-экрана UI (TICKET-13).

## Out of scope

- Реальный backup/restore перед первой записью — Phase 2 (записи ещё нет в этом тикет-сете).
- Kill-switch на `Protocol error` — реализуется вместе с первым write-путём, Phase 2.

## Acceptance criteria

- [ ] `SafeCommandId` enum генерируется на этапе сборки из `opcode_acl` тестового/реального реестра.
- [ ] Compile-time seam test проходит: реестр с `destructive`-опкодом не производит соответствующий `SafeCommandId`-вариант (проверяется через `trybuild`/аналог или интеграционный тест над codegen-выводом).
- [ ] Исполнитель (execute-путь) в `psafety` принимает только `SafeCommandId`, не сырой `u8`/`Vec<u8>` — типобезопасность проверяется компилятором, не рантайм-проверкой.
- [ ] Journal фиксирует каждый вызов через `psafety` (пока — только read-вызовы из TICKET-12).

## Verification plan

`cargo test -p psafety`, включая codegen seam test; интеграционный тест: попытка вручную сконструировать вызов исполнителя с raw opcode не компилируется (`trybuild`-тест на "не компилируется").

## TDD classification

REQUIRED (это ровно тот случай — "concurrency/safety-critical state transition" — из списка TDD required в скилле).

## Expected architecture impact

Реализует REQUIRED_BEFORE_IMPLEMENTATION-финдинг architecture review (SafeCommandId compile-time seam) для будущих write-тикетов; для Phase 1 (read-only) — закладывает контракт, не весь функционал.

## Risks

- Codegen на build.rs, читающий SQLite/YAML на этапе сборки — усложняет build-граф (нужен доступ к `data/` во время `cargo build`). Зафиксировать как implementation note, если потребует xtask вместо build.rs.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
