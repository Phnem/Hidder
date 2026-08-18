# TICKET-15 (EPIC): Verified write + Analog Monitor (Phase 2)

## Status

PENDING (BLOCKED by TICKET-10..14; TICKET-08 выполнен, TICKET-09 в remote-треке и гейтом не является)

## Objective

Соответствует Phase 2 плана ("Запись + аналог"): verified write для actuation/RT с backup и подтверждением через аналоговый стрим; Analog Monitor UI; restore/undo/dry-run.

## User or system value

Первая запись в устройство — ядро продукта. AC4 из `spec.md`.

## Dependencies

Кодовые тикеты Phase 3 (TICKET-10..14) плюс уже выполненные TICKET-07/08 должны быть DONE или DONE_WITH_DEVIATIONS. **TICKET-09/06 гейтом не являются** (уточнено 2026-08-17: они в параллельном remote-validation треке). Дополнительно требует решения архитектурного финдинга "SafeCommandId codegen owner" (architecture review п.6, REQUIRED_DURING_IMPLEMENTATION), который был сознательно отложен до этого эпика.

## BLOCKER, добавлен 2026-08-18 (TICKET-12): кто вправе звать raw transport write

TICKET-12 реализовал первый реальный обмен и по дороге зафиксировал findng, который безопасен для read-only и **не безопасен для write**.

`ptransport::ProbeChannel::write_report` принимает байты и обязан их принимать: `ptransport` — низ DAG и не может зависеть от `psafety`, поэтому типом «только то, что сминтил gate» он параметризоваться не может. Rust здесь не выразит friend-crate границу. Пока весь путь read-only, цена этого — комментарий в двух местах; с появлением первой записи цена другая.

**До первой production-записи workspace обязан получить механическую проверку**, а не соглашение:

- raw HID write call-sites допустимы **только** в явно разрешённом transport-адаптере;
- у верхних крейтов (`pproto`, `pcore`, feature-слои) не должно быть произвольного прямого пути `-> ptransport.write(raw bytes)`;
- проверка выполняется на build/CI (grep-уровня architecture test достаточно, если он падает), потому что типами это в текущем DAG не выражается.

Не блокирует закрытие read-only TICKET-12. Блокирует старт этого эпика.

## Scope (эпик-уровень, будет декомпозирован на вертикальные тикеты на архитектурном чекпоинте перед стартом)

- `write()`/`verify()` для `he.actuation`/`he.rt.*` в AULA protocol engine, с полным backup-перед-первой-записью (§10.4 плана) и kill-switch на `Protocol error` (§10.7 плана).
- Analog Monitor: `he.analog_stream` capability, если TICKET-08 подтвердил доступность vendor-defined TLC под Windows (иначе — зафиксировать как ограничение, не тихо пропустить).
- Restore/undo через Journal (TICKET-11) — журнал уже пишет каждую команду, здесь добавляется откат.
- Троттлинг flash-записей — per-family параметр из `quirks` (не глобальная константа, см. доменное правило `spec.md`).

## Out of scope

- ROYUAN write-путь — отдельно в TICKET-17 (второе семейство), не смешивать с AULA-веткой этого эпика.
- Learning Mode Уровень 2/3 (Safe Probe/Verified Write для *неизвестных* устройств) — это TICKET-16, не этот эпик (этот эпик — про уже `Verified` AULA family).

## Acceptance criteria (эпик-уровень; детальные критерии появятся при декомпозиции)

- [ ] Слайдер actuation/RT в UI реально меняет порог срабатывания на реальном железе.
- [ ] Изменение подтверждено аналоговым стримом (или задокументированной альтернативной стратегией verify, если стрим недоступен для этой платы — см. риск в TICKET-08).
- [ ] Backup создаётся автоматически перед первой записью; restore работает одной кнопкой.
- [ ] Rate limiter предотвращает percussive flash writes (регрессионный тест на троттлинг).

## Verification plan

Hardware-in-the-loop на AULA Hero 84 HE; будет детализирован per-тикет при декомпозиции.

## TDD classification

REQUIRED для кодеков записи и rate-limiter логики; будет переклассифицировано per-тикет при декомпозиции.

## Expected architecture impact

Финализирует `SafeCommandId` write-путь (первое реальное использование сверх read-only). Требует архитектурного чекпоинта (см. skill's "Architecture checkpoints" — "после коherent группы foundational тикетов", т.е. после Phase 1) перед стартом.

## Risks

См. таблицу рисков плана (§15): "окирпичивание чужой платы записью" (критический), "успешный ответ на неподдержанную команду" (очень высокая вероятность до verify).

## Implementation notes

Empty before implementation. **Этот тикет намеренно не декомпозирован на вертикальные срезы в этом плановом проходе** — по правилам ticket-autopilot декомпозиция на уровне, который ещё не наступил (Phase 1 не завершена), рискует стать умозрительной; финальная декомпозиция происходит на архитектурном чекпоинте после Phase 1.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
