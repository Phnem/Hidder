# TICKET-12: Первый protocol engine (AULA) — read-only

## Status

PENDING

## Objective

Реализовать `ProtocolEngine` для AULA Hero 84 HE family в read-only режиме: `probe`/`capabilities`/`read` для `he.actuation`, `he.rt.down`, `he.rt.up`, `he.rt.continuous` через `safe_read`-опкоды, определённые через Learning Mode Уровень 0/1 (Inventory + Guided Capture, §8 плана) поверх результатов TICKET-08.

## User or system value

Это первый сквозной вертикальный срез продукта: реальная плата → приложение опознаёт её → показывает реальные значения. Прямо реализует AC2 из `spec.md` (Phase 1 DoD).

## Dependencies

TICKET-08 (TLC-инвентарь), TICKET-10 (fingerprinting/confidence), TICKET-11 (SafeCommandId — read-опкоды тоже должны идти через типобезопасный gate).

## Scope

- Learning Mode Уровень 0 (Inventory) применительно к AULA: собрать TLC/report descriptors/строки — уже частично есть из TICKET-08, здесь оформляется в `.heprofile`-подобный локальный артефакт для разработчика.
- Learning Mode Уровень 1 (Guided Capture) для actuation/RT: сценарий "поставь 0.5мм → Apply → запиши пакет" через **официальный вендорский софт** (не наше приложение — мы только слушаем/сравниваем), минимум 3–4 точки, вывод гипотезы формулы (byte offset, scale) с R².
- Реализация `read()` в `ProtocolEngine` для AULA family на основе подтверждённой (не гипотетической) гипотезы.
- `probe()`/`capabilities()` возвращают только то, что подтверждено (`origin: Verified(hw)`), не `Assumed`.

## Out of scope

- `write()`/`verify()` для записи — Phase 2 (эпик issue 15), не этот тикет.
- Keymap/RGB/макросы — не блокируют HE-first v1 (см. spec, "Required behavior" п.3 — по мере покрытия, не обязательно в Phase 1).

## Acceptance criteria

- [ ] Приложение (через `pcore`, ещё без UI) при подключении AULA Hero 84 HE возвращает `confidence: verified` через `pregistry`.
- [ ] `capabilities()` перечисляет как минимум `he.actuation`, `he.rt.down`, `he.rt.up`, `he.rt.continuous` с `origin: Verified(hw)`.
- [ ] `read()` для каждой из них возвращает значение, совпадающее с тем, что реально выставлено на клавиатуре (кросс-проверка через официальный вендорский софт или физическое измерение, где возможно).
- [ ] Все опкоды, использованные в `read()`, проходят через `SafeCommandId` (TICKET-11), не через сырой opcode.

## Verification plan

Hardware-in-the-loop: ручная кросс-проверка со значениями, выставленными через официальный AULA-софт. Property-тест на кодек (encode/decode round-trip) для формата пакета, если формат достаточно прост для этого на данном этапе.

## TDD classification

RECOMMENDED (repository/protocol-engine contract — TDD там, где практично; сама Guided Capture часть по природе исследовательская и не TDD-driven, но `read()`-кодек после того, как формула подтверждена, — TDD-способен).

## Expected architecture impact

Первый реальный потребитель `ProtocolEngine` trait — по architecture review (п.5), после этого тикета трейт можно считать validated для одного семейства, но не финальным (валидация вторым семейством — Phase 2/4 эпик после ROYUAN-трека).

## Risks

- Guided Capture зависит от наличия официального AULA-софта и возможности его снифать/патчить (§8 плана, Уровень 1) — если официальный софт защищён от снифинга сильнее, чем ожидалось, тикет может застрять на этом шаге; в таком случае — эскалация в `WAITING_FOR_USER_DECISION` с описанием альтернатив (USBPcap/Wireshark сниффер, см. §8 плана).

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
