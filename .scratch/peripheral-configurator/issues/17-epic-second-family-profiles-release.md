# TICKET-17 (EPIC): Второе семейство (ROYUAN) + профили + публичный релиз v0.1 (Phase 4)

## Status

PENDING (BLOCKED by TICKET-09, TICKET-15)

## Objective

Соответствует Phase 4 плана: довести ROYUAN protocol engine до состояния verified read+write (не только TLC-инвентарь из TICKET-09), реализовать `pprofile` (кросс-брендовые пресеты, process watcher), опубликовать v0.1 (Win + Linux) с read-only для неопознанных устройств.

## User or system value

Первое реальное подтверждение "универсальности" архитектуры — второе, структурно отличное семейство, работающее через тот же `ProtocolEngine`. Прямая реализация ключевого продуктового аргумента ("AULA + ROYUAN-бренд, один пресет, одна кнопка", §9 плана).

## Dependencies

TICKET-09 (ROYUAN TLC-инвентарь), TICKET-15 (verified write паттерн уже отработан на AULA — переиспользуется, не изобретается заново).

## Scope (эпик-уровень)

- ROYUAN protocol engine: probe/capabilities/read/write/verify, используя факты из TICKET-01 (sharkfin protocol doc, facts-only) и собственную Learning Mode верификацию (TICKET-16 методология), **не копируя код sharkfin** (ADR-0001).
- `pprofile`: кросс-девайсные пресеты, capability-graceful degradation (см. architecture review, FOLLOW_UP п.7), hardware vs software профиль.
- Process watcher: opt-in, whitelist, отдельный поток.
- Публичный релиз v0.1: Windows + Linux (macOS — открытый вопрос по объёму, см. `spec.md` WebHID SAFE_DEFAULT "после Phase 2" — уточнить актуальность на момент декомпозиции).
- Read-only режим по умолчанию для любого устройства с `confidence < verified` — это уже реализовано архитектурно в TICKET-10/11, здесь — проверка на реальном релизе с реальными пользователями.

## Out of scope

- Мыши — Phase 6 (TICKET-19), не этот эпик.
- Ingestion pipeline — Phase 5 (TICKET-18), параллельный трек, не блокирует и не блокируется этим эпиком напрямую.

## Acceptance criteria (эпик-уровень)

- [ ] Один пресет применяется на AULA и ROYUAN плату одновременно (метрика успеха §16 плана, Phase 4).
- [ ] ≥2 protocol family, ≥50 моделей `Verified` или `HighConfidence` в реестре (метрика §16 плана — вероятно требует и вклада TICKET-18/16, зависит от темпов community submissions).
- [ ] Публичный релиз доступен, неопознанные устройства открываются read-only без крашей.

## Verification plan

Будет детализирован при декомпозиции; включает публичный релизный чеклист (final scope audit по правилам скилла).

## TDD classification

Смешанно, финализируется при декомпозиции.

## Expected architecture impact

Второй реальный потребитель `ProtocolEngine`/`CapValue` — по architecture review это точка, после которой трейт можно считать действительно validated (не только AULA-специфичным).

## Risks

sharkfin/другой проект может занять нишу первым (риск §15 плана, средняя вероятность/средний ущерб) — смягчается тем, что позиционирование HE-first ориентировано именно на незанятую часть.

## Implementation notes

Empty before implementation. Декомпозиция откладывается до архитектурного чекпоинта после TICKET-09/15.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
