# TICKET-10: Многосигнальный fingerprinting устройства (`pregistry`)

## Status

DONE_WITH_DEVIATIONS (2026-08-18); **architecture correction внесена 2026-08-18** после ревью — см. раздел Correction

## Objective

Реализовать `pregistry`: схему реестра, источник данных, и **матчер с раздельными осями идентичности** и explicit confidence на каждой.

### Отказ от единого линейного ranking (решение пользователя, 2026-08-18, по данным TICKET-22)

Спецификация ранжирует сигналы одной цепочкой: хеш дескриптора → TLC → строки → identify-опкод → fw-версия → VID:PID («слабейший, только индекс»). TICKET-22 показал, что этого недостаточно: у VXE ресивер и сама мышь отдают **побайтово одинаковые дескрипторы и одинаковый набор TLC**, а различаются ровно тремя «слабейшими» сигналами — PID, product string, release.

Единая шкала здесь не просто неточна — она даёт уверенно неверный ответ: матчер с максимальным весом на дескрипторе объявит ресивер и мышь одним и тем же, и будет «уверен».

Поэтому линейное ranking **отменяется**. Вместо него — минимум три независимые оси, у каждой свои сигналы и свой confidence:

```text
1. endpoint / structural identity
   «какой формы этот HID-endpoint, каким протоколом он в принципе может говорить»
   сигналы: набор TLC (usage page/usage), хеши дескрипторов, число интерфейсов,
            профиль report ID и размеров
   у VXE wired и VXE receiver — ОДИНАКОВАЯ

2. physical product identity
   «с каким конкретно физическим продуктом я сейчас разговариваю»
   сигналы: VID:PID, manufacturer/product strings, release (bcdDevice)
   у VXE wired и VXE receiver — РАЗНАЯ

3. protocol-family identity
   «каким словарём опкодов это говорит»
   сигналы: утверждение реестра с собственным evidence-маркером;
            позже — safe identify-опкод (это probe, не в этом тикете)
   сегодня для всех трёх устройств — UNKNOWN, и это честный ответ
```

Оси не складываются в одно число и не перетекают друг в друга. Совпадение по структуре **не имеет права** повышать confidence продукта — это ровно та ошибка, которую делает линейная шкала.

Третья ось отдельно важна для безопасности: гейт записи (`psafety`, TICKET-11) должен получать confidence именно **protocol-family**, а не «мы узнали устройство». Узнать продукт и не знать его протокол — нормальное и частое состояние.

## User or system value

Прямая реализация ключевого доменного правила спецификации: "family нельзя вывести из PID". Без этого TICKET-12 (первый protocol engine) не может безопасно решить, можно ли доверять устройству чтение/запись.

## Dependencies

TICKET-08 (реальные TLC/дескрипторы AULA), TICKET-22 (topology мыши и ресивера во всех режимах), TICKET-07, TICKET-11 (гейт, куда доезжает confidence).

## Scope

- Схема реестра по `spec.md` Приложение B, с разделением сигналов по трём осям.
- Источник данных — человекочитаемые файлы в `data/devices/`, генерируемые в код на этапе сборки (как `data/protocols/` в TICKET-11).
- Записи для трёх реально наблюдавшихся endpoint'ов: AULA Hero 84 HE, VXE wired, VXE receiver. Данные — из артефактов TICKET-08/22, без ручного переписывания цифр.
- Матчер: на вход — наблюдение уровня перечисления, на выход — по одному исходу на каждую ось, **с объяснением, какие сигналы совпали и какие разошлись**, а не bool и не одно число.
- Confidence-шкала (`unknown | candidate | high | verified`), общая для осей по форме, но считаемая независимо.
- Доставка family-confidence в `SafetyGate::identify_device` (`psafety`).

### Обязательный regression test

**VXE wired vs VXE receiver.** Одинаковая HID-структура, разные physical-device identities. Матчер обязан:

- вернуть **один и тот же** structural id для обоих;
- вернуть **разные** product id;
- ни при каких весах не выдать «это одно и то же устройство»;
- не повысить product-confidence из-за совпадения структуры.

Этот тест — не иллюстрация, а причина, по которой тикет переписан. Он должен падать, если кто-нибудь вернёт линейную шкалу.

## Out of scope

- Запись EPOMAKER в реестр — данные придут из remote-валидации (TICKET-09). Тикет этим **не блокируется**: запись добавляется отдельным follow-up, когда артефакт получен.
- Мышиный протокольный слой, `MouseProtocol`, DPI/polling/кнопки — фаза 6 (TICKET-19). Из TICKET-22 сюда приходит только identity и topology, не протокол.
- **Любые active probes.** Никаких identify-опкодов, никаких feature-чтений, ничего отправляемого в устройство. Матчер работает исключительно на данных уровня перечисления. Сигнал «safe identify opcode» из спецификации остаётся неиспользованным до тех пор, пока не появится тикет, которому разрешено слать команды (TICKET-12+), и ось protocol-family до тех пор честно отвечает `unknown`.
- UI для отображения confidence — TICKET-13.

## Acceptance criteria

- [x] `pregistry` компилируется; источник в `data/devices/` превращается в код на этапе сборки.
- [x] В реестре присутствуют три реально наблюдавшихся endpoint'а с данными из артефактов TICKET-08/22.
- [x] Матчер возвращает **три независимых исхода** (structural / product / family), каждый со своим confidence и со списком совпавших и разошедшихся сигналов.
- [x] **Regression: VXE wired и VXE receiver дают один structural id и разные product id.**
- [x] Совпадение по структуре не повышает product-confidence — проверено тестом, а не комментарием.
- [x] Ось protocol-family отвечает `unknown` для всех трёх устройств, потому что evidence нет; никакая другая ось не может её повысить.
- [x] Family-confidence доезжает до `SafetyGate::identify_device`, и гейт отказывает в записи ниже `verified`.
- [x] Ни один active probe не выполняется и не может быть выполнен из этого крейта.
- [x] Unit-тест на доменное правило «PID переиспользуется»: одинаковый VID:PID при разном дескрипторе даёт разные structural identity.
- [x] Ни одна принадлежность к protocol family не выведена из VID:PID, бренда или строки.

## Verification plan

`cargo test -p pregistry`; ручная проверка, что реальный AULA report descriptor (из TICKET-08) матчится с `confidence: verified` после ручного ввода его как эталона.

## TDD classification

REQUIRED (детерминированная логика подсчёта весов сигналов — canonical case для TDD согласно правилам скилла).

## Expected architecture impact

Формирует финальный публичный контракт `pregistry` (не только схему, но и matcher API), на который будет опираться TICKET-12.

## Risks

- Веса сигналов (§6.2 плана) — эвристика. После TICKET-22 она проверяется на **двух физических устройствах разных категорий** и нескольких topology, что заметно лучше одной клавиатуры, но всё ещё далеко от рынка. Третье устройство (EPOMAKER) придёт удалённо и позже (TICKET-09). Явно зафиксировать как «первая итерация, пересмотреть после первого remote-артефакта и после Phase 4/5» в implementation notes при закрытии тикета.

## Correction (2026-08-18, по итогам ревью)

Три правки модели. Матчер намеренно не переписывался: изменились инварианты и типы, а не алгоритм.

### 1. Family больше не логически недостижима без product

Было: family искалась **только** через product, и неизвестный SKU означал `NoProductMatch` — тупик. Это неверно: вендор регулярно отгружает ту же прошивку под новым product id, и незнакомый SKU не то же самое, что незнакомый протокол.

Стало — два независимых маршрута:

```text
через product        registry claim, capped by product confidence
                     (claim — это факт, записанный О ПРОДУКТЕ)

независимо           ProtocolEvidence { family, confidence, source }
                     НЕ capped: обмен, установивший семейство, установил его
                     о том, что на проводе, а не о названии в таблице
```

`ProtocolEvidenceSource`: `VerifiedExchange` (обмен с устройством, ответ проверен, доступно с TICKET-12) либо `VendorArtifact` (конфигуратор/прошивка/захват трафика, привязанные к endpoint). Evidence **передаётся вызывающим** — крейт сам с устройствами не разговаривает; он решает, чего это evidence стоит.

При наличии обоих маршрутов выигрывает более уверенный; при равенстве — protocol evidence, потому что оно о протоколе.

`FamilyReason::NoProductMatch` переименован в `NoEvidence`: причина теперь «ни продукта, ни protocol evidence», а не «нет продукта».

**Structural match по-прежнему не подтверждает family ничем.** Это отдельный тест, и он остался.

### 2. Verified теперь axis-scoped на уровне типа

`Confidence::permits_write()` **удалён**. Универсального «можно ли писать» больше не существует: голое значение confidence не помнит, о чём оно, и это ровно та ошибка, которую стоило сделать невозможной.

Появился `pcaps::FamilyConfidence` — единственный тип с `permits_write()`. `SafetyGate::identify_device` и `Refusal::UnverifiedFamily` принимают только его. Сконструировать его из чужой оси всё ещё технически можно, но пишется это как `FamilyConfidence::established(product.confidence)` — заметно в ревью и находится грепом, а не выглядит обычной сантехникой.

### 3. Обязательный regression

`an_unknown_product_can_still_have_a_known_family`: неизвестный SKU + независимо подтверждённое protocol evidence → **family identified, product остаётся Unknown**, запись разрешена. Плюс `protocol_evidence_is_not_capped_by_the_product`, `weak_protocol_evidence_does_not_beat_a_solid_registry_claim`, `structure_alone_still_confirms_no_family_whatsoever`.

### Открытый checkpoint: хранилище реестра

Сгенерированная Rust-таблица принята как **временная** реализация. До TICKET-18 / первых community submissions обязателен выбор: либо сгенерированный SQLite согласно исходной архитектуре (`spec.md` Приложение B), либо отдельный ADR с обоснованным изменением решения. Внесено в повестку архитектурного чекпоинта; тикет не считается закрывающим этот вопрос.

## Implementation notes

### Модель evidence

```text
observation (только перечисление)
   |
   +--> structural axis ----> StructuralId = digest(interfaces, sorted collections)
   |      сигналы: interface_count, collection_set, descriptor_digest, report_profile
   |      совпал digest -> verified;  совпал набор TLC, но не байты -> candidate
   |
   +--> product axis -------> запись реестра
   |      сигналы: vendor_id, product_id, manufacturer_string, product_string, release
   |      всё сошлось -> verified;  VID:PID сошлись, строка/release нет -> candidate
   |
   +--> protocol-family axis -> заявление реестра, найденное ЧЕРЕЗ product
          сигнал: registry_claim
          confidence = min(evidence заявления, confidence продукта)
          нет продукта -> NoProductMatch;  нет заявления -> NotRecorded
```

Оси не складываются и не перетекают. Три отдельных правила, каждое проверено тестом:

1. **Структура не поднимает product.** Endpoint со знакомой байт-в-байт структурой и незнакомым VID:PID даёт `structural: verified` + `product: unknown`.
2. **Family ищется только через product.** Совпадение структуры не даёт добраться до family-заявления — `FamilyReason::NoProductMatch`.
3. **Family не может быть увереннее продукта.** `min(claim, product)`: verified-заявление о candidate-продукте даёт candidate.

Из третьего правила следует то, ради чего всё это: **знание продукта не является разрешением на запись.** Гейт спрашивает confidence именно family-оси, и у всех трёх наших устройств она `unknown`.

### Что различает сигналы

`Signal::axis()` — каждый сигнал принадлежит ровно одной оси, и это тип, а не соглашение. Сигнал, который «немного помогает» двум осям, — признак непродуманности, а не удобство.

`SignalState` различает **три** состояния, а не два: `Matched`, `Differed`, `Absent`. Разошедшееся значение — свидетельство против; отсутствующее — свидетельство ни о чём. Строка, которую устройство не отдало, и строка, которая не совпала, считаться одинаково не должны.

### Результат на реальном железе

`cargo run -p pregistry --example identify`:

```text
AULA Hero 84 HE   structural 4677b3488a43cfc9 [verified]  shared by: aula-hero84-he
                  product    aula-hero84-he    [verified]
                  family     unknown           [unknown]   (product known, family never established)
                  write permitted: no

VXE wired         structural bbcbc7e8c2828786 [verified]  shared by: receiver, wired
                  product    ...-wired         [verified]
                  family     unknown           [unknown]
                  write permitted: no

VXE receiver      structural bbcbc7e8c2828786 [verified]  shared by: receiver, wired
                  product    ...-receiver      [verified]
                  family     unknown           [unknown]
                  write permitted: no
```

Два VXE-endpoint имеют **один и тот же** structural id и **разные** product id. Именно это линейная шкала выразить не могла.

### Отсутствие probes — свойство типа

`DeviceObservation` строится единственным конструктором `from_enumeration(...)`, и в нём нет поля, которое могло бы прийти из обмена с устройством. Сигнал «safe identify opcode» из спецификации остаётся неиспользованным; когда появится тикет, которому разрешено слать команды, он приедет отдельным полем с собственным именем — и тогда будет видно, какие выводы от него зависели.

### Доставка в гейт

`SafetyGate::identify_device(device, family, family_confidence)`. Гейт отклоняет write-класс при confidence ниже `Verified` (`Refusal::UnverifiedFamily`), **до** проверки backup — чтобы пользователю показывалась настоящая причина, а не «нет бэкапа». Чтения при этом разрешены: устройство с неустановленным семейством открывается read-only, а не «никак».

## Deviations

1. **Реестр не в SQLite.** Спецификация (Приложение B, SAFE_DEFAULT) предполагает YAML → сгенерированный SQLite. Реализовано TOML → сгенерированная таблица Rust, как в TICKET-11. Причины: (а) SQLite тянет C-зависимость и не покупает ничего на трёх записях; (б) парсер реестра — часть того, что решает, на какую прошивку направлен гейт записи, поэтому нужен поддерживаемый парсер с `deny_unknown_fields`; (в) API матчера работает со срезом `&[DeviceEntry]`, а не с базой, поэтому подмена хранилища на SQLite позже не ломает вызывающих. Схема Приложения B по существу сохранена, изменился носитель.
2. **TOML вместо YAML** — та же причина, что в TICKET-11 (единственный YAML-парсер экосистемы Rust помечен deprecated).
3. **`Confidence` живёт в `pcaps`, а не в `pregistry`.** `psafety` не может зависеть от `pregistry` (оба — соседи по DAG), а слово нужно обоим. Второй enum во втором крейте разъехался бы с первым, и разъезд был бы тихим и относящимся к безопасности.
4. **Записи реестра сгенерированы из артефактов захвата скриптом**, а не набраны руками: числа переписывать нельзя, ошибка транскрипции здесь неотличима от находки. Человеческие поля (name, brands, kind, note) добавлены вручную.
5. **Сигнал «safe identify opcode» не реализован.** Он требует probe, а probes в этом тикете запрещены. Ось protocol-family поэтому опирается только на заявление реестра — и сегодня честно пуста.

## Review findings

Самопроверка. Блокирующих findings нет.

Три незакрывающих замечания:

1. **Ни у одного устройства нет protocol family, поэтому путь «family → verified → запись» не проверен на реальных данных** — только на синтетических записях в юнит-тестах. Первое реальное заполнение произойдёт в TICKET-12, и именно там правило впервые что-то разрешит.
2. **Структурная ось объявляет `Verified` при точном совпадении digest.** Это опирается на то, что все записи реестра получены с нашего железа. Когда появятся community-записи (TICKET-18), у структуры тоже понадобится собственный evidence-маркер, иначе чужая запись будет давать ту же уверенность, что своя.
3. **`Candidate` для структуры (совпал набор TLC, не совпали байты) пока не встречается ни на одном реальном устройстве** — это ветка «прошивку обновили», проверенная только синтетикой.

## Completion evidence

- 37 тестов в `pregistry` (30 unit + 7 против реальных захватов), 3 в `pcaps`, 68 в `psafety` после доработки. Всего в workspace 109.
- Обязательный regression прогоняется **на реальных файлах захвата**, а не на транскрипции: `crates/pregistry/tests/real_hardware.rs` читает `docs/hardware/*.json` через `include_str!`.
- Воспроизводимая демонстрация: `cargo run -p pregistry --example identify` — печатает три оси и список сработавших сигналов для каждого устройства.
- Полная верификация: `cargo fmt --all --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace`, `cargo deny --all-features check licenses bans sources`, `python scripts/check_crate_dag.py` — все зелёные.
- Ни одного обращения к устройству: тикет читал только файлы.
