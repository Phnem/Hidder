# TICKET-19 (EPIC): Мыши (Phase 6)

## Status

PENDING (BLOCKED by TICKET-17)

## Objective

Соответствует Phase 6 плана: `MouseProtocol` trait, Sinowealth-семейство первым (больше всего открытого знания), `ReceiverMouse` для 2.4 ГГц ресиверной адресации.

## User or system value

Завершает кросс-девайсный продуктовый аргумент: "клавиатура + мышь, один пресет" работает не только гипотетически.

## Dependencies

TICKET-17 (архитектура `ProtocolEngine`/`pprofile` уже validated двумя keyboard-семействами; мышь — третий, структурно другой класс устройства — не начинать, пока паттерн не устоялся на клавиатурах).

## Scope (эпик-уровень)

- `MouseProtocol` trait — на основе дизайна libratbag (MIT, можно использовать код напрямую по ADR-0001), адаптированного под `ProtocolEngine`/`CapValue` контракты этого проекта.
- Sinowealth-семейство первым (наибольшее открытое знание — `gloriousctl`/`sinowealth-*` утилиты из prior-art инвентаризации TICKET-03).
- `ReceiverMouse { receiver, device_index }` — транспорт-уровень для HID++ через ресивер с адресацией устройства (уже заложено в исходном наброске плана).
- Заполнение `mouse.*` capability-namespace (уже зарезервирован в `pcaps` с самого начала — architecture review, NOT_RELEVANT_TO_SCOPE для Phase 0/1, актуально теперь).

## Out of scope

- RGB-«канвас» на весь ПК — вне scope всего проекта (не только этого эпика, см. `spec.md` "Явно НЕ в v1").

## Acceptance criteria (эпик-уровень)

- [ ] Кросс-девайсный пресет «клавиатура + мышь» работает на реальных устройствах (метрика §16 плана / DoD Phase 6).

## Verification plan

Будет детализирован при декомпозиции; потребует покупки минимум одной Sinowealth-мыши (аналогично TICKET-06 для клавиатур).

## TDD classification

Финализируется при декомпозиции.

## Expected architecture impact

Первая реальная проверка, что `ProtocolEngine`/`CapValue`, спроектированные для клавиатур, обобщаются на структурно другой класс устройства (мышь: DPI stages, LOD, motion sync — не HE-концепты вообще).

## Risks

Если `ProtocolEngine` окажется недостаточно общим для мышей — потребуется архитектурная ревизия, а не просто новый engine. Флагируется заранее как риск, не решается сейчас.

## Implementation notes

Empty before implementation. Декомпозиция откладывается до архитектурного чекпоинта после TICKET-17.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
