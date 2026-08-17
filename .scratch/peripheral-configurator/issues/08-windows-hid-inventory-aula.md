# TICKET-08: Ключевой эксперимент — HID-инвентарь AULA Hero 84 HE на Windows

## Status

READY (TICKET-07 done). **Главный тикет фазы 2** — primary reference device, плата в наличии.

## Objective

Реализовать `ptransport::enumerate()` через `hidapi` (Windows-backend — `hid.dll`/Win32 HID API) и вывести полный HID-инвентарь AULA Hero 84 HE на Windows: какие TLC реально открываются, какие дают `ERROR_ACCESS_DENIED`, доступен ли vendor-defined TLC (и, если да, содержит ли он аналоговый стрим). Если `hidapi` не даёт доступа к нужной информации об TLC/access-статусе — использовать прямой Win32 (`CreateFile`+`HidD_*`) как **локальный escape hatch внутри `ptransport`**, не как основной путь (см. `spec.md` FR7).

## User or system value

Это фундаментальный эксперимент, от которого зависит вся Windows-часть архитектуры (§5, §18 шаг 6 плана). Пока он не сделан, предположение "hidapi/Win32 открывает vendor-defined TLC как у sharkfin" — гипотеза, а не факт. Подтверждает или опровергает AC3 из `spec.md`.

## Dependencies

TICKET-07 (workspace/crate skeleton) — **выполнен**.

Замечание из ревью TICKET-07, адресованное этому тикету: `hidapi::HidError` уже протекает в `TransportError` как источник `#[from]`. Это тип ошибки, а не handle, доступа к устройству он не даёт — но при добавлении реального I/O проверить, что вместе с ним не начали протекать другие типы `hidapi`.

## Scope

- `ptransport::enumerate()` — перечисление всех HID top-level collections устройства через `hidapi`, без утечки `hidapi::HidDevice`/Win32-типов наружу крейта (см. architecture review §6/§9).
- Enumeration-путь не обязан ещё владеть устройством через полноценную `DeviceSession` (это read-only разведочный код), но должен возвращать данные через типы, совместимые с будущим `DeviceSession`-контрактом из TICKET-07/11 — не через голый `HidDevice`.
Снимается (уточнено 2026-08-17 — этот же список дословно повторяет TICKET-09 для второго устройства, чтобы отчёты были сравнимы построчно):

```text
все HID-интерфейсы
top-level collections
usage page / usage
VID / PID
manufacturer / product strings
report descriptor (сырой hex) + его хеш
report IDs
размеры report'ов
наличие feature / input / output report'ов
что открывается через hidapi
что не открывается и с какой ошибкой
```

И отдельно — четыре вопроса, на которые тикет обязан ответить явно, включая ответ «нет» и «не удалось определить»:

```text
есть ли config vendor TLC?
есть ли analog vendor TLC?
это одна коллекция или разные?
понадобился ли Win32 escape hatch?
```

- Для каждого TLC: попытка открытия + результат (успех / `ERROR_ACCESS_DENIED` / другая ошибка); при отказе — повторная проверка под elevated-процессом.
- Эвристика для vendor-defined канала: usage page `0xFF00`–`0xFFFF`. Это эвристика поиска, а не признак семейства — совпадение с параметрами ROYUAN (`0xFFFF`, usage 2) ничего не доказывает о принадлежности AULA, и обратное тоже.
- Результат — дамп в `EXECUTION_LOG.md` и человекочитаемый отчёт; сырые данные (hex дескриптора) сохраняются целиком, а не в пересказе: они станут входом для fingerprinting в TICKET-10.

**Никаких write-команд**, ни при каких условиях, включая «просто проверить, отвечает ли».

## Out of scope

- Интерпретация протокола (что означают байты) — это Learning Mode, TICKET-12+.
- Запись каких-либо данных в устройство — этот тикет **read-only enumeration only**, никаких `WriteFile`/`HidD_SetFeature`.

## Acceptance criteria

- [ ] Полный список TLC устройства с usage page/usage и статусом доступа зафиксирован, по всем полям из Scope.
- [ ] На все четыре вопроса из Scope дан явный ответ.
- [ ] Формат отчёта пригоден для построчного сравнения со вторым устройством (TICKET-09) — это требование к форме, а не пожелание: сравнительная таблица является артефактом фазы 2.
- [ ] Явно отвечено: доступен ли vendor-defined TLC AULA Hero 84 HE через `hidapi` (или, если потребовался escape hatch, через прямой Win32) без прав администратора.
- [ ] Если недоступен без admin — повторная проверка под elevated-процессом, результат зафиксирован (§5 плана: "elevated не гарантированно помогает — тоже проверить эмпирически").
- [ ] Результат явно помечает AC3 (`spec.md`) как подтверждённый/опровергнутый для AULA-платы.

## Verification plan

Ручной запуск на реальном железе разработчика (Windows). Результат — не автоматический тест (зависит от физического устройства), а зафиксированный в `EXECUTION_LOG.md` и `data/protocols/aula.md` (или аналог) отчёт с сырыми данными (hex report descriptor).

## TDD classification

NOT_NEEDED (эмпирический эксперимент над реальным железом, не детерминированная бизнес-логика; сам код перечисления TLC может получить unit-тесты на парсинг report descriptor позже, в TICKET-10/12)

## Expected architecture impact

Определяет финальную форму `ptransport`'s public API (`DeviceId`/`SessionHandle`-совместимые типы, `TlcDescriptor` и т.п.) — первый реальный потребитель контракта, заложенного в TICKET-07. Также первая точка, где решается, нужен ли escape hatch на прямой Win32 в принципе, или `hidapi` достаточно для всех нужных данных (report descriptor, access-статус per-TLC) — фиксировать это решение в Implementation notes при закрытии тикета.

## Risks

- Аналоговый стрим может сидеть в той же коллекции, что и стандартный keyboard input report → недоступен через hidapi так же, как через WinRT (открытый вопрос Q2 в `spec.md`). Если так — зафиксировать честно, не предполагать успех.
- Результат специфичен для конкретной прошивки AULA Hero 84 HE — зафиксировать версию прошивки вместе с результатом.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
