# TICKET-09: HID-инвентарь ROYUAN-платы на Windows (кросс-семейная проверка)

## Status

BLOCKED

## Objective

Повторить эксперимент TICKET-08 на купленной ROYUAN-плате, чтобы проверить, совпадает ли поведение Windows/hidapi с AULA и с ожиданием "как у sharkfin через WebHID" (§5 плана — это распространено по аналогии, не факт).

## User or system value

Один пример (AULA) не доказывает общность вывода про доступность vendor-defined TLC через `hidapi` (или Win32-escape-hatch, если он потребовался в TICKET-08). Второе, структурно другое семейство — минимальное условие, чтобы считать вывод архитектурным фактом, а не совпадением одной платы (см. architecture review, риск №3 "premature trait finalization" — тот же принцип применим и здесь).

## Dependencies

TICKET-06 (устройство должно быть куплено и получено), TICKET-08 (переиспользует `ptransport::enumerate()`).

## Scope

Идентичен TICKET-08, применительно к ROYUAN-плате: полный TLC-инвентарь, статус доступа для каждого usage page/usage, явная проверка vendor-defined TLC (ожидаемо `0xFFFF`, usage 2, 64-байтные feature reports, report ID 0 — по данным sharkfin, см. `docs/prior-art/royuan.md` из TICKET-01).

## Out of scope

Интерпретация протокола, запись — те же ограничения, что в TICKET-08.

## Acceptance criteria

- [ ] Полный TLC-инвентарь ROYUAN-платы зафиксирован тем же форматом, что TICKET-08.
- [ ] Явное сравнение с AULA: совпадает ли доступность vendor-defined TLC.
- [ ] Явное сравнение с ожиданием sharkfin/WebHID (usage page `0xFFFF`, usage 2) — подтверждено или опровергнуто для реальной купленной платы.

## Verification plan

Ручной запуск на реальном железе разработчика (Windows), результат фиксируется в `EXECUTION_LOG.md` + `data/protocols/royuan.md`.

## TDD classification

NOT_NEEDED

## Expected architecture impact

Если поведение расходится с AULA — `ptransport`'s API, спроектированный в TICKET-08 только под один случай, может потребовать доработки до того, как на него будут писаться протокол-engines (TICKET-12 и далее для ROYUAN-трека).

## Risks

- Заблокирован логистикой покупки (TICKET-06) — не блокирует остальной Phase 1 AULA-трек (см. `MASTER_PLAN.md`).
- Купленная модель может не оказаться structurally "чистым" ROYUAN несмотря на предположение по VID — сам этот тикет является частью проверки этого предположения.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
