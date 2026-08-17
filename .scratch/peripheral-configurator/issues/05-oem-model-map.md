# TICKET-05: Список топ-моделей HE-клавиатур и OEM-карта

## Status

PENDING (blocked by TICKET-01, TICKET-03)

## Objective

Собрать список ~20 самых массовых HE-моделей на рынке и определить их OEM/protocol-family принадлежность (ROYUAN vs другие), с явным включением AULA Hero 84 HE (референсная плата в наличии).

## User or system value

Даёт приоритизацию для будущей ingestion pipeline (Phase 5) и для выбора, какая вторая ROYUAN-плата покупается (TICKET-06) — без слепого выбора наугад.

## Dependencies

TICKET-01 (частично — понимание ROYUAN-семейства), TICKET-03 (prior-art инвентаризация).

## Scope

- Таблица: модель | бренд(ы) | предполагаемый OEM/protocol family | источник предположения (VID:PID / sharkfin registry / product page) | confidence (низкий, т.к. на этом этапе никакой матчинг ещё не верифицирован железом).
- Явно отметить AULA Hero 84 HE и её вероятное protocol family (для планирования TICKET-08).

## Out of scope

- Верификация family реальным железом — это TICKET-08/09/10, не этот тикет (данные здесь — гипотезы, не `Verified`).

## Acceptance criteria

- [ ] `docs/prior-art/oem-map.md` содержит ≥20 моделей с колонками из Scope.
- [ ] AULA Hero 84 HE присутствует с явной пометкой "reference board — TICKET-08".
- [ ] Ни одна строка не помечена как `confidence: verified` — на этом этапе максимум `candidate`/`high` по шкале §6.2 плана.

## Verification plan

Ручная — таблица существует и покрывает заявленный охват.

## TDD classification

NOT_NEEDED

## Expected architecture impact

Прямой вход для будущих `data/devices/*.yaml` (Phase 1/4), но сам тикет не создаёт код/данные реестра — только исследовательскую таблицу.

## Risks

Оценки OEM по продуктовым страницам ненадёжны (задокументированный риск плана: PID переиспользуется, family нельзя вывести из PID) — явно маркировать низкую confidence, не выдавать за факт.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
