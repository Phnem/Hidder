# TICKET-14: `tools/emu` device emulator + первый CI-тест fingerprint→family

## Status

PENDING

## Objective

Собрать `tools/emu` из записанных ответов AULA Hero 84 HE (полученных в TICKET-08/12) и поставить первый CI-тест "fingerprint → ожидаемое семейство", чтобы CI не зависел от физического железа на раннере.

## User or system value

Без эмулятора CI не может проверять протокольную логику вообще — hardware-in-the-loop не масштабируется на CI-раннеры. Это прямое условие Test seams из `spec.md` ("Device emulator — единственный способ иметь CI без физического железа").

## Dependencies

TICKET-08 (нужны реальные записанные ответы устройства), TICKET-10 (fingerprint matcher, который тест проверяет).

## Scope

- `tools/emu`: подставной HID-девайс за тем же `ptransport`-контрактом (`DeviceSession`/`SessionHandle` по `DeviceId`, не голый handle — см. architecture review §6/§9), воспроизводящий записанные ответы AULA.
- Функция "врать как настоящий": на неизвестный/неподдержанный опкод возвращает предыдущий ответ (не ошибку) — воспроизводит задокументированное поведение реальных плат (§0 плана, "reply is not evidence").
- CI-тест: эмулированное устройство → `pregistry` matcher → ожидаемый `confidence: verified` и правильная `protocol_family`.

## Out of scope

- Эмуляция ROYUAN — добавляется отдельным follow-up, когда TICKET-09 даст реальные данные (не блокирует этот тикет).
- Fuzzing report descriptor парсера — упомянуто в Test seams `spec.md`, но отдельный follow-up, не входит в это тикет.

## Acceptance criteria

- [ ] `tools/emu` запускает подставное устройство, отвечающее записанными пакетами AULA.
- [ ] `ptransport`/`pregistry`/protocol-engine код из TICKET-10/12 работает против эмулятора **без изменений** (это и есть проверка, что абстракция `ptransport` не завязана на реальное железо).
- [ ] CI-тест "fingerprint → family" зелёный на всех трёх ОС без физического устройства.
- [ ] Эмулятор корректно воспроизводит "неподдержанная команда → предыдущий ответ" (регрессионный тест на анти-фикция-сценарий).

## Verification plan

`cargo test -p emu` + CI на трёх ОС; ручная проверка, что тот же код пути (protocol engine из TICKET-12), направленный на эмулятор вместо реального устройства, даёт идентичный результат.

## TDD classification

REQUIRED (детерминированное поведение — "возвращает записанный ответ", "имитирует неподдержанную команду" — canonical TDD case).

## Expected architecture impact

Подтверждает (или опровергает) архитектурное решение TICKET-07/08 не завязывать `ptransport`-контракт на реальные Win32/hidraw типы — если эмулятор не может встать за тот же интерфейс без хаков, это сигнал, что абстракция протекает, и требует ревизии перед тем, как на неё будут писаться дальнейшие protocol engines.

## Risks

Нет существенных сверх обычных рисков тестовой инфраструктуры.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
