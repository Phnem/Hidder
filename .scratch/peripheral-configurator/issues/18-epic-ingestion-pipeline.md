# TICKET-18 (EPIC): Device Intelligence Pipeline / `tools/ingest` (Phase 5)

## Status

PENDING (BLOCKED by TICKET-17 не строго — может стартовать раньше как параллельный трек, но требует стабильной схемы реестра из TICKET-10)

## Objective

Соответствует Phase 5 плана: JS/asar анализатор веб-конфигураторов вендоров, кластеризация → protocol family candidates, массовый импорт device definitions без покупки железа на каждую модель.

## User or system value

Единственный реалистичный способ вырасти с десятков до сотен моделей в реестре без покупки сотен плат (метрика §16 плана).

## Dependencies

TICKET-10 (стабильная схема реестра/fingerprint), TICKET-04 (лицензионная гигиена ADR-0001 — этот эпик по своей природе работает с чужими артефактами, правила §7.3/§14 плана применяются напрямую).

## Scope (эпик-уровень)

- `tools/ingest` (уже изолирован от релизной сборки с TICKET-07): `fetch/`, `unpack/`, `classify/`, `extract_js/`, `extract_bin/`, `cluster/`, `report/` по структуре §7.2 плана.
- Юридическая гигиена (§7.3 плана, обязательна с первого коммита этого эпика): source+license+hash+дата на каждый ingest-артефакт; никогда не распространять чужие бинарники/прошивки; анализ бинарников — только в изолированной VM без сети и проброса USB.
- Кластеризация по метрике similarity (§7.2 плана) → `ProtocolFamilyCandidate` с confidence, никогда не автопромоушен в "можно писать" — только человек подтверждает после hardware round-trip.

## Out of scope

- Автоматическая запись в устройства на основе ingest-данных — противоречит доменному правилу "писать можно только при confidence >= Verified", ingest даёт максимум `candidate`/`high`.

## Acceptance criteria (эпик-уровень)

- [ ] База выросла с десятков до сотен моделей без покупки дополнительного железа (метрика §16 плана, Phase 5).
- [ ] Каждый ingest-артефакт имеет зафиксированные source/license/hash/дату.
- [ ] Ни один автоматически найденный `ProtocolFamilyCandidate` не помечен `verified` без человеческого hardware-подтверждения.

## Verification plan

Будет детализирован при декомпозиции.

## TDD classification

Смешанно (кластеризация — детерминированная логика, REQUIRED; анализаторы бинарников — RECOMMENDED). Финализируется при декомпозиции.

## Expected architecture impact

Не влияет на релизные крейты напрямую (изолирован по дизайну с TICKET-07) — влияет только на объём и качество `data/devices/`.

## Risks

Юридическое письмо от вендора (§15 плана, низкая вероятность/средний ущерб) — митигация: §14 плана, не хостить чужие бинарники, вести себя как приличный бот при краулинге (robots.txt, rate limit, User-Agent с контактом).

## Implementation notes

Empty before implementation. Декомпозиция откладывается до отдельного архитектурного чекпоинта, специфичного для этого эпика (может начаться независимо от Phase 4, если приоритеты изменятся).

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
