# TICKET-02: Связаться с автором sharkfin

## Status

PENDING (blocked by TICKET-01)

## Objective

Отправить автору sharkfin (`dniminenn`) сообщение: представиться, спросить про статус HE-опкодов (сейчас "не отвечает/отклонено прошивкой" в PROTOCOL.md) и предложить обмен протокольными находками по железу. Явно обозначить, что проект строится независимо и закрыто (proprietary) — без ожиданий совместного кода или лицензии.

## User or system value

Может сэкономить месяцы реверс-инжиниринга, если у автора уже есть непубликованные HE-находки. Даже отрицательный ответ снимает неопределённость и позволяет двигаться дальше без ожидания.

## Dependencies

TICKET-01 (нужно прочитать протокол, чтобы задать содержательные вопросы, а не общие).

## Scope

- Один email/issue с: (1) кто мы, (2) что строим (HE-first конфигуратор), (3) явно — proprietary, не просим GPL-код, интересует обмен фактами о протоколе/железе, (4) конкретный вопрос про HE-опкоды.
- Зафиксировать факт отправки и любой ответ в `EXECUTION_LOG.md`.

## Out of scope

- Ожидание ответа не блокирует остальные тикеты Phase 0/1 — это независимая ветка (см. решение интервью: "cooperation on information, not code").

## Acceptance criteria

- [ ] Сообщение отправлено, зафиксирована дата и канал (email/GitHub issue) в `EXECUTION_LOG.md`.
- [ ] Если получен ответ — законспектирован как факт с evidence-маркером `FromVendorJs`/`FromCommunity` по аналогии с §7.3 плана; код из ответа (если приложен) не используется (ADR-0001).

## Verification plan

Ручная — запись в `EXECUTION_LOG.md` с датой отправки существует независимо от того, пришёл ли ответ.

## TDD classification

NOT_NEEDED

## Expected architecture impact

Нет.

## Risks

- Автор не ответит — не блокер, помечается как принятый риск, не как BLOCKED-тикет.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
