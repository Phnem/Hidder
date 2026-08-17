# TICKET-10: Многосигнальный fingerprinting устройства (`pregistry`)

## Status

READY (TICKET-07 и TICKET-08 выполнены; идёт после TICKET-11 по порядку основного code path)

## Objective

Реализовать `pregistry` со схемой из `spec.md` Приложение B и многосигнальным matcher'ом (report-descriptor hash → TLC-набор → manufacturer/product strings → safe identify-опкод → fw-версия → VID:PID), с explicit confidence, семя данными — **реальные данные AULA Hero 84 HE из TICKET-08**: descriptor topology, TLC, VID/PID, strings, report IDs и размеры, release, vendor-коллекции. Впервые реестр строится не теоретически, а против настоящего устройства.

**Обновлено 2026-08-17 (вторая мутация дня):** тикет **не ждёт второго устройства**. EPOMAKER находится у стороннего владельца и переведена в remote-validation трек (TICKET-09/06); её fingerprint-запись добавляется отдельным follow-up, когда придёт удалённый артефакт. Одного устройства достаточно, чтобы построить схему, matcher и веса сигналов; ограничение «веса эвристичны, проверены на одном устройстве» фиксируется явно (см. Risks), а не обходится ожиданием.

## User or system value

Прямая реализация ключевого доменного правила спецификации: "family нельзя вывести из PID". Без этого TICKET-12 (первый protocol engine) не может безопасно решить, можно ли доверять устройству чтение/запись.

## Dependencies

TICKET-08 (нужны реальные TLC/report descriptor данные AULA для первой fingerprint-записи); TICKET-07.

## Scope

- SQLite-схема, сгенерированная из YAML (`data/devices/aula-hero84-he.yaml` как первая запись, по шаблону §19.1 плана).
- `fingerprint` таблица с весами сигналов (report_descriptor_sha256 — самый сильный, VID:PID — самый слабый, индекс).
- Confidence enum: `unknown | candidate | high | verified` (как в шаблоне устройства §19.1).
- Правило: запись в устройство разрешена только при `confidence >= verified` для конкретной `protocol_family` — сам gate реализуется в `psafety` (TICKET-11), но `pregistry` обязан отдавать confidence наружу как часть контракта.

## Out of scope

- Запись второго устройства в реестр — данные придут из remote-валидации (TICKET-09). Тикет этим **не блокируется**: вторая запись добавляется отдельным follow-up, когда артефакт получен.
- UI для отображения confidence — TICKET-13.

## Acceptance criteria

- [ ] `pregistry` компилируется, YAML → SQLite build-шаг работает.
- [ ] Первая реальная запись устройства (AULA Hero 84 HE) с данными из TICKET-08 (report descriptor hash, TLC-набор) присутствует в `data/devices/`.
- [ ] Matcher возвращает confidence и объясняет, какие сигналы совпали (не просто true/false).
- [ ] Unit-тесты: matcher корректно различает два устройства с одинаковым VID:PID, но разным report descriptor hash (регрессионный тест на "PID переиспользуется" из доменных правил).

## Verification plan

`cargo test -p pregistry`; ручная проверка, что реальный AULA report descriptor (из TICKET-08) матчится с `confidence: verified` после ручного ввода его как эталона.

## TDD classification

REQUIRED (детерминированная логика подсчёта весов сигналов — canonical case для TDD согласно правилам скилла).

## Expected architecture impact

Формирует финальный публичный контракт `pregistry` (не только схему, но и matcher API), на который будет опираться TICKET-12.

## Risks

- Веса сигналов (§6.2 плана) — эвристика, проверенная **на одном устройстве**. Второе физическое устройство недоступно локально (remote-трек, TICKET-09), поэтому «различает ли matcher два разных устройства» проверяется синтетически (unit-тест с одинаковым VID:PID и разным descriptor hash) и эмулятором (TICKET-14), а не живым железом. Явно зафиксировать как «первая итерация, пересмотреть после первого remote-артефакта и после Phase 4/5» в implementation notes при закрытии тикета.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
