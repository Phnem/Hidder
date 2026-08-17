# TICKET-10: Многосигнальный fingerprinting устройства (`pregistry`)

## Status

PENDING

## Objective

Реализовать `pregistry` со схемой из `spec.md` Приложение B и многосигнальным matcher'ом (report-descriptor hash → TLC-набор → manufacturer/product strings → safe identify-опкод → fw-версия → VID:PID), с explicit confidence, семя данными — AULA Hero 84 HE и EPOMAKER (обновлено 2026-08-17: покупка ROYUAN-платы отменена; вторым устройством выступает имеющаяся EPOMAKER). Прямой вход в этот тикет — сравнительный отчёт двух устройств из TICKET-09: он показывает, какие сигналы фактически различают наше железо, а какие бесполезны.

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

- ROYUAN-запись в реестр — зависит от TICKET-09 (данные появятся, когда плата куплена и проинвентаризована); тикет не блокируется этим, просто ROYUAN-запись добавляется отдельным follow-up, когда данные готовы.
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

- Веса сигналов (§6.2 плана) — эвристика, не проверенная на множестве устройств (у нас пока 1-2). Явно зафиксировать как "первая итерация, пересмотреть после Phase 4/5" в implementation notes при закрытии тикета.

## Implementation notes

Empty before implementation.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
