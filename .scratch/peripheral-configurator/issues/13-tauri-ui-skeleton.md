# TICKET-13: Tauri UI skeleton — Devices + HE (read-only) + Journal

## Status

PENDING

## Objective

Собрать минимальный Tauri+React UI с тремя экранами: Devices (карточка устройства, статус подключения), HE (read-only отображение actuation/RT значений из TICKET-12), Journal (список исполненных `SafeCommandId` из TICKET-11).

## User or system value

Первая точка, где результат Phase 1 виден не только разработчику в консоли, но и как реальный продукт — прямая демонстрация AC2 из `spec.md`.

## Dependencies

TICKET-07 (app skeleton существует), TICKET-12 (нужны реальные данные для отображения), TICKET-11 (Journal-данные).

## Scope

- Экран Devices: карточка AULA Hero 84 HE, статус "подключено"/"нет устройства"/"конфликт с вендорским софтом" (детекция конфликта — минимальная эвристика на этом этапе, не полная реализация).
- Экран HE: визуальный layout клавиатуры (можно упрощённый placeholder-layout на этом этапе, не полноценный KLE-импорт — это SAFE_DEFAULT из `spec.md`, реализуется позже), отображение actuation/RT значений per-key, помеченных `origin`.
- Экран Journal: таблица исполненных команд с таймстампом.
- IPC между UI и `pcore` (Rust) закладывается сразу тремя механизмами (`spec.md` FR11, architecture review §6): Tauri **commands** для запросов вида "получить состояние Devices/HE/Journal", Tauri **events** для низкочастотных уведомлений (`device_connected`/`device_disconnected`/`protocol_error`), и зарезервированный, но пока пустой канал **Channels** для будущего `he.analog_stream` — этот тикет не реализует Analog Monitor, но обязан не завести никакой высокочастотный поток через общий event bus, который потом придётся переделывать.
- Дизайн-принцип из spec.md соблюдён с первого экрана: неподтверждённые (`Assumed`) capability не показываются как рабочий контрол.

## Out of scope

- Редактирование значений (write) — Phase 2, не этот тикет (UI в этом тикете строго read-only, симметрично TICKET-12).
- Analog Monitor, Profiles, Learning Mode экраны — появляются в соответствующих будущих эпиках (issues 15/16).

## Acceptance criteria

- [ ] Приложение запускается, показывает реальное состояние AULA Hero 84 HE при подключении (не мок-данные).
- [ ] HE-экран показывает только capability с `origin: Verified(hw)`; ничего не показывается как интерактивный контрол записи.
- [ ] Journal-экран отображает реальные записи из `psafety`.
- [ ] Отключение устройства корректно отражается в UI (не зависает на последнем известном состоянии молча).

## Verification plan

Ручное тестирование в браузере/Tauri-окне с реальным устройством: подключить → увидеть карточку и значения → отключить → увидеть смену статуса. Frontend unit-тесты на IPC-контракт (моканое `pcore`).

## TDD classification

RECOMMENDED (IPC-контракт и state-management — интеграционный шов, TDD где практично; чисто визуальная вёрстка — NOT_NEEDED, см. классификацию плана).

## Expected architecture impact

Формирует трёхканальный IPC контракт `pcore` ↔ UI (commands/events/channels), который будет расширяться, но не переосмысливаться радикально в последующих эпиках (write, Analog Monitor) — при условии, что Channels зарезервированы под высокочастотные потоки уже сейчас, а не введены постфактум в Phase 2.

## Risks

Нет существенных сверх обычных UI-рисков.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
