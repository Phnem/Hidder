# TICKET-06: Купить ROYUAN HE-клавиатуру

## Status

PENDING (blocked by TICKET-05)

## Objective

Заказать и получить одну HE-клавиатуру из ROYUAN-семейства (VID `0x3151` наиболее вероятен, либо `0x379a`/`0x374a`/`0x38a9`/`0x046a`/`0x2ea8`/`0x145f` — см. §0 плана), чтобы иметь второй, структурно отличный от AULA Hero 84 HE, protocol family для верификации архитектуры.

## User or system value

Без второго OEM-семейства весь `ProtocolEngine`-trait и `pregistry`-fingerprinting проектируются вслепую на одном примере — прямой риск, отмеченный в architecture review (см. `architecture/INITIAL_REVIEW.md`, п.3).

## Dependencies

TICKET-05 (желательно выбрать конкретную модель по итогам OEM-карты, а не наугад).

## Scope

- Выбрать конкретную модель из ROYUAN-семейства с наиболее полным protocol doc покрытием в sharkfin (снижает риск при Learning Mode на TICKET-09).
- Заказать, дождаться доставки.
- Зафиксировать в `EXECUTION_LOG.md`: точная модель, VID:PID (по факту получения), дата.

## Out of scope

- Работа с устройством (enumeration, fingerprinting) — TICKET-09, не этот тикет.

## Acceptance criteria

- [ ] Устройство физически получено.
- [ ] Подключается по USB (кабель — ROYUAN-класс не имеет config-канала по 2.4ГГц/BT, см. §5 плана "Wireless").
- [ ] VID:PID зафиксирован в `EXECUTION_LOG.md`.

## Verification plan

Физическая — устройство на руках, определяется системой (Диспетчер устройств/`lsusb`/аналог) с ожидаемым VID.

## TDD classification

NOT_NEEDED

## Expected architecture impact

Разблокирует TICKET-09 (Windows HID inventory для ROYUAN) и весь ROYUAN-трек Phase 1.

## Risks

- Логистическая задержка — не блокирует остальной Phase 1 (AULA-трек идёт независимо, см. `MASTER_PLAN.md` зависимости).
- Купленная модель может оказаться не ROYUAN несмотря на предположение по VID (PID переиспользуется вендорами) — верифицируется только в TICKET-09, не раньше.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
