# TICKET-16 (EPIC): Learning Mode (Phase 3)

## Status

PENDING (BLOCKED by TICKET-15)

## Objective

Соответствует Phase 3 плана: Уровни 0–1 (inventory + guided capture + diff-гипотезы) как продуктовая фича (не только внутренний dev-инструмент, каким они были в TICKET-08/12), экспорт `.heprofile` + GitHub submission без бэкенда, Уровень 2 (safe probe с анти-фикция-фильтром).

## User or system value

Прямой путь к краудсорсингу длинного хвоста моделей (§7 плана) — без этого рост базы устройств ограничен ручной покупкой железа разработчиком.

## Dependencies

TICKET-15 (verified write должен существовать и быть проверенным на известном семействе прежде, чем предлагать пользователям Verified Write на неизвестном).

## Scope (эпик-уровень)

- UI-раздел Learning Mode (отдельный "инженерный" раздел, не пугающий обычного пользователя — §11 плана).
- `.heprofile` экспорт (структура §8 плана) с обязательным приватность-фильтром (Test seams в `spec.md`).
- `Submit device` → генерация файла + предзаполненный GitHub Issue/PR, без backend.
- Уровень 2 Safe Probe: allowlist-only опробование, анти-фикция-фильтр (повтор ответа = "не поддержано").

## Out of scope

- Уровень 3 (Verified Write для неизвестных устройств, доведённый до продакшен-качества) — может быть частью этого эпика или следующего, решится при декомпозиции в зависимости от объёма.
- Лицензия device-данных сообщества (открытый вопрос Q1 в `spec.md`) — должна быть решена **до** включения публичного `Submit device`, но это отдельное решение, не блокирующее написание кода этого эпика заранее.

## Acceptance criteria (эпик-уровень)

- [ ] Второй человек с незнакомой платой присылает `.heprofile`, и модель добавляется в реестр без физического железа на руках у мейнтейнера (метрика успеха §16 плана, Phase 3).
- [ ] Приватность-тест (Test seams) подтверждает: ни один keystroke не попадает в capture-лог, для web- и desktop-сборки.
- [ ] Safe Probe никогда не пробует опкоды вне allowlist (регрессионный тест на "никакого 0x00–0xFF брутфорса").

## Verification plan

Будет детализирован при декомпозиции; включает внешний тест — реальный сторонний пользователь с незнакомым устройством.

## TDD classification

Смешанно — diff-гипотезы (детерминированная логика) REQUIRED; UI-часть NOT_NEEDED/RECOMMENDED. Финализируется при декомпозиции.

## Expected architecture impact

`plearn` крейт получает первую реальную реализацию (был крейтом-заглушкой с TICKET-07).

## Risks

Юридический вопрос Q1 (лицензия community-данных) должен быть закрыт до публичного запуска `Submit device` — см. `spec.md` Open Questions.

## Implementation notes

Empty before implementation. Декомпозиция откладывается до архитектурного чекпоинта после TICKET-15.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
