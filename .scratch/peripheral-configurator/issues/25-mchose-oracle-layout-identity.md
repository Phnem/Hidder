# TICKET-25: MCHOSE — ORACLE + LAYOUT + IDENTITY: живая семантика без железа

## Status

**Superseded by the entries below.** This line and the next section describe
the state as of the *first* pass (priorities 1–2 only, oracle/echo/sweep not
started, wired `0x04` `NOT_ESTABLISHED`). Every later consolidation pass in
this file, ending with wired `0x04` = `IMPOSSIBLE_AT_WIRE_LEVEL` and
`hardware_shaped_hole = EMPTY`, supersedes it. Left in place as history, not
current status — see the final `TICKET-25 FINAL A PREVIEW ELIGIBILITY PASS`
section at the end of this file for the current verdict.

### A PREVIEW BLOCKER = OPEN (as of this first pass only — see final section)

Приоритет 1 выполнен полностью — путь найден, прослежен и классифицирован, — но блокер **не закрыт**, и причина стала точнее, а не мягче.

**Что установлено (`docs/prior-art/mchose-static-lane.md`, коммит `c4392d4`):**

1. **Вывод TICKET-24 был неверен.** Там `setReset` классифицировали по его парсеру как «запись performance-записи, а не сброс устройства». Прослеживание UI-действия вместо парсера показало обратное: `setReset` **и есть** factory reset. Ошибка ровно того рода, против которого написан F-1 — судили по имени и парсеру, а не по пути, который до команды доходит.
2. **Полная цепочка**: `key==="restoreFactorySetting"` → confirm-диалог → `sendCommand("set","setReset",payload)` → `commandQueue` → `oy.getCommandConfig` → схема парсера → кадр → транспорт.
3. **Проводной путь — последовательность из трёх записей**: `setReset`, затем `setLightColor` с маркером `90,165` (= `0x5A A5`), затем `setDiyLight`. **Смещение маркера зависит от модели** — индекс 16 у одной группы моделей, 23 у другой. Тот же вид дискриминатора, что `payload[3]` у AULA.
4. **`"setReset"` встречается в бандле три раза** — определение и два вызова, оба из обработчика factory reset. Безобидного потребителя нет.

**Почему блокер остаётся OPEN (это и есть находка):**

```js
setPerformanceWiredParser:()=>j_(!0)
setResetWireParser:()=>j_(!0)
```

На проводном транспорте `setPerformance` и `setReset` имеют **одинаковый ведущий байт `04` и буквально одну и ту же функцию-парсер с тем же аргументом** — то есть идентичную раскладку кадра. Рутинная запись настроек и factory reset **неразличимы по форме кадра**; их разделяют только значения, и какие именно — не установлено. У AULA хотя бы `payload[3]` отличался структурно.

На радио они различимы (`04` против `06`, разные раскладки) — неоднозначность только проводная.

| команда | класс |
|---|---|
| `setReset` | **DESTRUCTIVE_CONFIRMED** |
| `setPerformance` (проводной) | **POTENTIALLY_DESTRUCTIVE** |
| `setLightColor` (вариант с `5A A5`) | **POTENTIALLY_DESTRUCTIVE** |
| `setPerformance` (радио) | не затронут |

Путь записи, который не может доказать, какую из двух команд он строит, — не классифицированный путь. Никаких реальных destructive-записей не выполнялось; всё получено статически.

### Приоритет 2 — 12 из 37, остальное unresolved

`DB/reports/protocol_knowledge/mchose/static/name_link.json`. Связи выведены **только там, где артефакт прямо утверждает обе половины**, с записью источника на каждую: `cardList.identities+desc` (полный HID-кортеж и описание в одной записи) и ключи `keyboardConfig` вида `0x3837_0x3033_MCHOSE G87 V2 2.4G`.

- resolved: **12** · ambiguous: **0** · unresolved: **25**
- Сопоставлений по похожести имён, по VID и по семейству **не делалось** — инструмент их структурно не умеет.
- Пофайловые пресеты (79 шт.) имени продукта не несут — проверено, там `switchOptions`/`precision`/`featSupport`/`deviceFlag`.

**Побочная находка, важная для будущего HE-слайса:** пресеты несут `switchOptions` с **`maxTravel` на каждый тип свитча** и `precision` — ровно та таблица, поиски которой у AULA закончились ничем. Для MCHOSE она опубликована пофайлово.

### Oracle-проход 2026-08-25 — инфраструктура работает, кадры не сняты

**Что получилось:**

1. **Fake-WebHID оракул построен и безопасен структурно.** В `runtime.js` добавлен маркер `__protocolMinerFake`, а `assert_no_real_hid()` проверяет **на живой странице**, что объект, в который вендорский код собирается писать кадры, — наш. До этого утверждение о безопасности держалось на том, что порядок инъекции сработал, то есть было допущением, а не проверкой.
2. **Наблюдённое ребро идентичности, воспроизведено дважды.** Фейковое устройство `0x3837:0x301a` (usagePage `0xff00`) → приложение показало **«God 60»**. Совпало с `cardList` («God 60 配置») и с записью в CZ-фильтрах — три независимых источника.
3. **Идентичность разрешается ПАССИВНО.** Имя показано при **нуле отправленных HID-кадров**. Для §0.2 («identity graph закрыт без ручного выбора») это прямое наблюдение: вендор закрывает идентичность из `vid:pid` + usage, без обмена и без выбора пользователем.
4. **Найден второй слой конфигурации — `cizhou`/`CZ_SHARED_DATA`**, невидимый ни обходу бандла, ни configCenter: `HidIndexDeviceFilters` (176 записей, **126 различных `vid:pid`, 10 VID, 7 usage page** — `0xff01/0xff0b/0xff00/0xff04/0xff02/0xff80/0x0001`), `deviceList`, `KEYBOARD_MODELS`, `czDeviceName`. Перенос «одной вендорской usage page» из выборки `cardList` был бы ошибкой.
5. **Детектор эха/EVIDENCE_VOID и сборщик UI-инвентаря написаны.** Детектор сравнивает тело целиком и не консультирует объявленную длину; эхо пустого запроса — `UNKNOWN`, не `ECHO` и не `SAFE`. Инвентарь структурно не умеет выдавать `NO_DESTRUCTIVE_PATH_FOUND` и не сворачивает `UNKNOWN` в `SAFE_READ`.

**Чего не получилось — и это блокирует приоритет 1:**

Оракул доходит до экрана выбора устройства и корректно кликает строку `God 60`, но **конфигуратор не открывается**, поэтому за все прогоны снято **0 кадров**. Без кадров нет byte-level diff, а значит:

> **Value-level discriminator для проводного `04` не установлен. `setPerformance`/`setReset` остаются неразличимыми по форме кадра, и wired `04` остаётся `POTENTIALLY_DESTRUCTIVE`.**

Сопутствующее: сайт грузится примерно в одном прогоне из трёх (добавлен ретрай навигации), и два дефекта харнесса дали **ложные находки**, а не ошибки — поиск кнопки по локализованной подписи и фиксированная пауза 4 с, из-за которой существующий контрол отчитывался как отсутствующий. Оба зафиксированы в коде.

### Проход 2026-08-25 (вечер) — CONFIGURATOR ENTRY = **CLOSED**

Вывод выше про «конфигуратор не открывается» **закрыт и был неверно
атрибутирован**: дело не в клике. Полный статический путь и живой trace —
`DB/reports/protocol_knowledge/mchose/CONFIGURATOR_ENTRY.md`, коммит `03b8e67`.

Строка устройства инертна, пока `SingletonDeviceStore` не отдаст состояние:
`connectMode` пишется в элемент списка только внутри `if (deviceState)`, а
`toDeviceAliveStatus(item).isDisabled` истинно, пока `connectMode == null`.
CZ SDK устаканивается около **t+21 с** — позже любой фиксированной паузы, что
использовал оракул. Ожидание по состоянию, а не по часам, снимает вопрос целиком.

Два дефекта харнесса снова дали **ложные находки**, а не ошибки:

1. **WebHID grant — per-origin, а не per-realm.** MCHOSE отдаёт конфигуратор
   клавиатур отдельным Next.js-приложением на `/cizhou/` **в iframe**. Тот фрейм
   не зовёт `requestDevice` и живёт на гранте origin'а. При per-realm флаге он
   опрашивал `getDevices()` ~10 раз в секунду и падал с `No HID devices found` —
   что читается как «вендор не поддерживает это устройство». Исправлено,
   закреплено тестом, проверенным на падение при откате.
2. **`0xff00:0x0001` — это BOOTLOADER, а не config channel.** Вендорский
   `isBootUpdateMode` говорит это для **каждой** карточной пары, и живое
   приложение согласилось: вместо конфигуратора открылся диалог обновления
   прошивки. Оракул гонял DFU-идентити на всех трёх профилях, отсюда и нулевой
   захват — рантайм закрывает почти всё через `if (isUpgradeMode) return`.
   Это **перенос формы с AULA**, причём и там он был неверен: у AULA
   config-коллекция была `0xFF60:0x0061` на том же pid.

Нормальные идентити (`isBootUpdateMode == False`): God 60 `0x3020`,
Ace 75 8K `0x303c`, Ace 75 16K `0x301d`, Ace 68 GT `0x3007`,
Ace 60 Pro ISO FR `0x3040`.

**Кадры пошли.** С нормальным pid приложение пишет `sendReport(rid=0, 64 B)`
`55 03 00 38 38 00…`. Это **пятое** семейство: не одно из четырёх, разделённых в
static lane, и код CZ SDK вообще отсутствует в собранном корпусе. Записано как
наблюдение, **не** слито с `hpe`.

**Блокер переехал, а не закрылся.** `setPerformance`/`setReset` (проводной `04`) —
команды семейства **BY** (`navigator.deviceHandler`, таблица `hpe`). God 60,
Ace 75, Ace 68 GT — все **CZ** (`webdriverEnum.subType == 2`). Byte-diff требует
BY-клавиатуры и синтетического ответа `getBattery`, выведенного из схемы `hpe` и
помеченного `synthetic_from_vendor_schema`.

### Identity — 22 из 25 закрыты вендорским источником

`mchose_cz_identity_sweep.py` спрашивает собственные lookup'ы CZ SDK. **66 из 125**
идентити резолвятся в **21** продукт; 22 из 25 ранее нерешённых keyboard-id
получили имя от вендора. Остаются `0x3837:0x302d`, `0x3837:0x3030`,
`0x3837:0x303e` — их нет ни в одной CZ-записи фильтра.

Остальные **59** записаны как `SDK_FALLBACK_NOT_A_NAME`: `getDeviceName` никогда
не возвращает пустоту, и её дефолт `"Ace60"` при буквальном чтении заявил бы, что
все мыши, гарнитуры и BY-клавиатуры MCHOSE называются «Ace60».

### CONSOLIDATION PASS 2026-08-25 — CZ-семейство раскрыто

Коммит `2025867`. Детали: `DB/reports/protocol_knowledge/mchose/CZ_FAMILY.md`.
101 тест зелёный. Real writes не выполнялись.

**CZ SDK — пятая кодовая база**, отдельный webpack-билд на
`/cizhou/CZ_SHARED_DATA/`, грузится в same-origin iframe. Собран по вендорскому
`asset-manifest.json` (53 артефакта, sourcemap'ов нет), поэтому полнота — это
список вендора, а не результат обхода. **Next-приложение** `/cizhou/_next/` —
другое дерево и **не собрано**; именно оно блокирует остальное.

**Envelope доказан** (`miner/static/mchose_cz_codec.py`): `[flag, command, 0,
sum&0xFF, size, off_lo, off_hi, 0, …]`, offset 16-bit LE, packet 64 B, report id 0.
Не просто переписан — воспроизводит захваченный кадр байт в байт, и тест
привязан к захваченным байтам. Что кадр **не** доказывает, записано рядом:
sum-vs-xor различается только по исходнику lodash, потому что на нулевом payload
они совпадают.

**Handshake проходит.** Ответ, собранный из вендорской схемы, доводит рантайм до
`isBusy:false, communicateEnabled:true, failedCount:0` и до маршрута
конфигуратора. Каждый такой ответ штампуется `synthetic_from_vendor_schema` в
момент создания, echo-аудит читает штамп → все 11 кадров `EVIDENCE_VOID`. Это
правильный результат: установлена структура **запросов**, и ничего о железе.

**Своя ambiguity, независимо от BY.** Байт 4 — это «сколько запрошено» для чтения
и «сколько передано» для записи, поэтому **CZ-чтение и CZ-запись нулями
байт-идентичны**. Одна корректная импликация, и только в одну сторону: вендорский
read-builder никогда не кладёт байты после 8-байтного заголовка, значит ненулевой
хвост доказывает запись. Обратное не доказывает ничего и отвергнуто. Из 9 CZ-команд
две — записи по наблюдению; `SAFE_READ` — **ноль**.

**Leakage gate поймал этот же проход**, когда оракул писал декодированный envelope
в файл захвата. Это верно: `oracle/` — корпус, и корпус со своей же интерпретацией
делает любое «движок восстановил структуру» нефальсифицируемым. Интерпретация
переехала в `analysis/`, exempt по роли, инвариант corpus-dirs не тронут.

**Identity закрыт.** Коллизий по `vid:pid` и по `vid:pid+usage` — **0**, граф
закрывается без ручного выбора. 22 из 25 id получили вендорское имя, 3 (`0x302d`,
`0x3030`, `0x303e`) не встречаются ни в одной CZ-записи и остаются unresolved, а не
угаданными. Два инварианта стали тестами: профиль не может указывать на DFU-identity,
и fallback-строка SDK никогда не считается именем.

**UI inventory пуст, и это записано как покрытие**: конфигуратор не рендерится на
синтетических нулях, поэтому найдено 0 контролов и выполнено 0. Ничто здесь не
утверждает, что у устройства нет destructive UI-пути, — только что по нему никто
не прошёл.

### CONSOLIDATION PASS 2 (2026-08-25, ночь) — CZ рендерится, BY пишет

Коммит `9e5d069`. 111 тестов зелёные. Real writes не выполнялись.
Детали: `DB/reports/protocol_knowledge/mchose/CZ_CONFIGURATOR.md`,
`DB/reports/protocol_knowledge/mchose/BY_FAMILY.md`.

**CZ payload semantics — ЗАКРЫТО.** Конфигуратор не рисовался из-за **одного**
ключа: вендорский God60-layout содержит `index: e => defaultKeyDict[252].index + 1`,
а этот dict ключуется кодами клавиш нулевого слоя. Все прочие коды деградируют в
`-1`, и только 252 бросает. Вендорский `default-keys-god-60.js` даёт 127 из 128
клавиш, вендорский `getKeyCode` говорит, какие байты дают 252 (`type=240,
code1=250`). **Куда** её положить — наш выбор, и манифест это фиксирует отдельно.
`3 × 128 = 384` совпало с размером региона, измеренным ранее по 56 кадрам со
stride `0x200`: источник и измерение сошлись независимо.

**UI inventory — ЗАКРЫТО (с честным покрытием).** 449 кадров, **13** командных
байт, **142 из 154** контролов пройдено. Четыре команды (`0x06`, `0x08`, `0x0b`,
`0xa0`) были невидимы, пока UI не поехал. Кэпы и бюджет прохода записаны в
артефакт: «найдено контролов» ≠ «контролов существует».

**Sweeps — ЗАКРЫТО для трёх полей.** CZ-команда `0x06`, record-offsets **9**, **20**
и **35**; последний кодируется как `value/30` в каждой наблюдённой точке. Каждое
поле подтверждено **дважды бесплатно**: CZ-запись шлёт одну 64-байтную запись
двумя чанками, поле всплывает на двух payload-offset'ах, и приведение к
record-offset их согласует. Какая секция UI владеет полями — **не утверждается**:
две секции показали одинаковые наборы контролов.

**Echo/EVIDENCE_VOID — ЗАКРЫТО.** Все 443 кадра прохода →
`SYNTHETIC_FROM_VENDOR_SCHEMA`, каждая команда → `EVIDENCE_VOID`. Это правильный
результат: установлена структура **запросов** и ничего о железе.

**BY поднят.** Проводное чтение — пара `sendFeatureReport`/`receiveFeatureReport`,
519 байт, report id 6; батарейный парсер кладёт уровень на сырой индекс 8 после
2-байтного среза `IG`. Приложение показало «K99, 100%, Кабельный» и открыло
**нативный Vue-конфигуратор**. Снято **10** проводных `04` `setPerformance`, поля
настроек — на смещениях **7, 8, 10, 22**; именно поэтому они и не могут быть
дискриминатором.

**Дефект, давший ложную находку:** 64-байтного ответа хватает для `getBattery`,
поэтому он выглядел правильным. Остальные чтения просят 504/512/128 байт;
короткие ответы уводили вендорские парсеры за границу (`Offset is outside the
bounds of the DataView` → `undefined.flat()`), состояние страницы не грузилось, и
**запись вообще не собиралась**. Подтверждение factory reset давало ноль кадров —
читается как «сброс ничего не шлёт», и это неправда.

**Диалог сброса найден и прочитан словами приложения:** «Эта операция приведёт к
сбросу всех настроек…» [Отмена] [Сброс]. **Кадр не снят** — `NOT_ESTABLISHED`.

### Не начато / осталось после consolidation

**BY wired `0x04` — ЗАКРЫТ ответом, а не обходом.** Коммит `78c42be`,
`DB/reports/protocol_knowledge/mchose/BY_0X04_INDISTINGUISHABLE.md`.

Диалог сброса **никогда не переставал открываться**. A/B по единственной
переменной (размер синтетического ответа, 64 против 519) дал тринадцать
одинаковых наблюдаемых состояний, включая `disabled` самой кнопки и попадание
клика. MutationObserver показал, что диалог открывался **в обоих** прогонах и
закрывался через секунды — все проверки со стороны драйвера приходили уже после.
Два моих прежних утверждения отозваны: зависимости от размера ответа не было, и
порядок проходов был ни при чём (хотя reset обязан идти последним — вендорский
handler заканчивается `router.push({name:"ConnectDevice"})`).

Первый diff сказал `DISCRIMINATOR_FOUND` по 40 смещениям — и был неверен: на
стороне `setPerformance` все они были нулями, а нули пришли от харнесса,
отвечавшего нулями на `getPerformance`. Он измерил харнесс.

Решающий эксперимент: отдать странице устройство, чья запись **и есть**
заводская, ответив на `getPerformance` захваченным reset-payload'ом с
выравниванием по вендорским парсерам (`reply[2:] = write[1:]`), затем через UI
переключить тумблер и вернуть обратно.

> `routine:toggle_back` **==** `reset_confirm`, **все 519 байт**.
> Промежуточный `toggle_on` отличается от обоих ровно одним смещением — 10.

Это структурно: у обеих команд `wiredCommand: "04"` и один и тот же сериализатор
`j_(!0)`, значит кадр — чистая функция данных; данные сброса — константа
`x8[model].otherObj`, то есть ровно то, что свежая клавиатура и так сообщает.
`setReset` — не отдельная команда на проводе.

**WIRED 0x04 DISCRIMINATOR = IMPOSSIBLE_AT_WIRE_LEVEL.** Следствия — правила, а
не оговорки: инспекцией кадра этот путь безопасным не сделать; безопасность
обязана обеспечиваться на уровне намерения (какая команда выдаётся); захваченный
`04`-кадр нельзя переигрывать никогда. `setPerformance` остаётся
`POTENTIALLY_DESTRUCTIVE`, и теперь это **окончательно**, а не «пока не выяснили».

9 регресс-тестов, один проверен на падение.

`hardware_shaped_hole` = **EMPTY**, и это не оплошность: ни одно оставшееся
утверждение не закрывается только физическим устройством.

### Детектор редеплоя сработал на первый же день

2026-08-25: уехали `mouseFirmwareHistory`, `otaConfigApp_prod`, `pre-newMouseConfig`. Бандл, `webVersion.hash`, `cardList`, `keyboardConfig` и `keyboardPreset` — **не** уехали, поэтому находки выше о живой сборке. Пофайловая гранулярность проверки окупилась немедленно.

## Objective

Получить живую семантику протокола MCHOSE, не имея ни одного физического устройства этого бренда: прогнать **настоящий** M HUB Web против fake-WebHID устройства, снять layout и identity по каждому продукту в скоупе, и выдать на выходе IR + per-capability Preview + машиночитаемую `hardware_shaped_hole`.

Playbook v4 §1.1, §1.2, §2.2, §2.3, §2.6.

## User or system value

Это тикет, который заменяет отсутствующее железо. Всё, что MCHOSE вообще может получить без физической платы, получается здесь. Он же — первая настоящая проверка того, что харнесс `pdevemu` + `fake_browser/runtime.js` семейно-нейтрален, а не заточен под AULA.

## Dependencies

**TICKET-23** (артефакты, каталог, границы семей) — жёстко.
**TICKET-24** (кодек и читатель ответов) — мягко: технически можно начать без него, но oracle-план без кодека вырождается в «покликать и посмотреть», а он должен заказывать конкретные записи и конкретные sweep'ы.

**Не даёт A Preview сам по себе** — см. TICKET-26 (§3: калибровочный гейт обязателен до первого signed bundle любой новой семьи).

## Scope

### 0. Аудит эха — ПЕРВЫМ ДЕЙСТВИЕМ (§1.1)

До любого анализа реплаев: для каждой пары `(opcode, sub) × source` посчитать `echo_rate` и **опубликовать таблицу**.

| echo_rate | статус источника |
|---|---|
| ≥ 0.95 | `EVIDENCE_VOID` — не входит в анализ реплаев |
| 0.5–0.95 | `EVIDENCE_SUSPECT` — только с весом < 1 |
| < 0.5 | пригоден |

`EVIDENCE_VOID` не значит «выбросить кадры»: TX-сторона остаётся валидной. Значит: гипотеза о семантике ответа, опирающаяся только на такие кадры, автоматически `UNTESTABLE`, а не `SUPPORTED`.

Прецедент, ради которого это стоит первым: 410 кадров из эмулятора AULA испортили целый frozen-прогон, и движок был не виноват.

### 1. Детектор эха по телу кадра (§1.2а)

```text
echoed(reply, request) := reply.body[HEADER..CHECKSUM] == zero_pad(request.payload, MAX_DATA_LEN)
```

Никакой зависимости от объявленной длины. Три обязательных регресс-теста: (1) настоящее эхо детектится, (2) пустой запрос с непустым ответом — **не** эхо, (3) изменение только байта длины не меняет вердикт.

И следствие §1.2б: **эхо с пустым payload неразличимо с «нечего сообщить»**. Такие команды классифицируются `unknown`, и **на них не строятся блокеры** — иначе ранг заблокирован навсегда по причине, которая не про знание.

### 2. Fake-WebHID против живого M HUB Web

Переиспользовать существующий харнесс (`pdevemu.session.ModelSession`, `fake_browser/runtime.js`, паттерн `aula_web_oracle.build_hero84_profile`), а не писать второй.

- **Ответ через макротаск** (`setTimeout`, дефолт 8 мс), не `Promise.resolve().then` — ловушка O-1, гонка same-microtask. Это generic-паттерн, уже в `runtime.js`.
- **Одна сессия на устройство** (O-5); закрытие сессии **дренирует очередь** (`clear_recv_queue`) и выдерживает cadence-паузу до следующего открытия (O-6).
- Профиль fake-устройства строится из каталога TICKET-23 — по одному прогону на модель, как `aula_multi_model_oracle` делает для 17 моделей AULA.

### 3. Вендорский IO-логгер как второй канал — с проверкой, а не на веру

M HUB **сам логирует свой HID-трафик** в консоль (`[webHID:接收Input]`, `[webHID:接收Feature]`, `[webHID] IO logger installed`). Это потенциально сильный независимый канал наблюдения.

**Но это вендорский код, и он может логировать не то, что уходит на провод** — после трансформации, до неё, с усечением, выборочно. Поэтому: логгер используется **только** после того, как его вывод сверен с нашим собственным перехватом `navigator.hid` на общем наборе кадров, и расхождения записаны. Логгер, совпавший с перехватом, — второй канал; логгер, не сверенный, — не evidence.

### 4. Правила съёма (ловушки O-2, O-3, O-4)

- **Sweep'ы:** enum — **все** значения; числовой — минимум **четыре** точки, исключающие более простую модель. Прецедент: две точки polling дали идеально проходящую и полностью неверную прямую (экстраполяция 500 Гц против правды 1000).
- **Record-команды: заставить UI записать РОВНО ОДНУ запись.** Тогда минимальная наблюдённая длина тела **и есть** stride. Это дешевле любой статистики: на данных AULA min-length дал 3/4 против 1/4 у column-consistency scoring, и единственный промах был пробелом сбора, а не дефектом правила.
- **Инвентарь UI-действий** с явным списком «**найдено, но не выполнено**». Этот список — часть выхода lane'а, наравне с трейсами. `NO_DESTRUCTIVE_PATH_FOUND` при непустом списке невыполненных действий — артефакт покрытия, а не факт. Здесь это особенно остро: TICKET-24 уже видит 27 × `factoryReset` в бандле.

### 5. LAYOUT LANE (§2.2)

- **`VendorKeyId` и `HidUsage` — два разных типа в IR. Никогда `u16` для обоих.** Конверсия только через извлечённую таблицу. Обязательный тест: `VendorKeyId::W.raw() != HidUsage::W.raw()` — если совпало, значит смотрим на одну таблицу дважды. Прецедент стоил двух exchange'ей: у AULA W = vendor 30, HID usage 26 (это `=`), и мы читали не те клавиши, получая их общий дефолт.
- **Layout не наследуется. Никогда**, даже внутри подтверждённой семьи (L-2).
- **ANSI/ISO/JIS-ветки** (L-3): в скоупе есть `Ace 60 Pro ISO-Nordic` и `Ace 60 Pro ISO-French` — региональные варианты уже присутствуют в каталоге витрины, искать ветвление по региону в вендорском коде.

### 6. IDENTITY LANE (§2.6)

Закрывается **первым** и **пассивно**.

- **`payload bytes` ≠ `wire bytes`** (I-1): число, входящее в fingerprint, обязано **перевыводиться из сырых байтов дескриптора** отдельным инструментом в CI. Тест, берущий обе стороны из одного источника, — не тест (у AULA это дало `candidate` вместо `verified` на трёх платах, и ничего не падало).
- `opened: true` ≠ «можем говорить» (I-2).

### 7. Выход

- IR-узлы с uncertainty на узел
- per-capability Preview eligibility
- **`hardware_shaped_hole` как машиночитаемый файл** (§0.3) — вход в rank-эвалюатор, а не документация. `A Preview` не выдаётся, если есть запись без `what_a_user_does_to_produce_one`.
- Блокеры, **зависящие от фактов, а не от класса команды** (§4.3): `if !SWITCH_TABLE_IS_PARSEABLE`, не `if !is_production_safe(...)`. Константа переключается **в том же коммите, что добавляет парсер**.
- Тест на блокеры утверждает **точный отрендеренный список**, не количество (§4.4).
- Проход 2' (§4.1): тот же сценарий против эмулятора, **засеянного реальными кадрами** (включая их несообразности — §4.2), плюс запись в `hardware_shaped_hole`, что физического прохода 2 не было. Preview, но **не** «closed».

## Out of scope

- Физическое железо MCHOSE — его нет.
- Rank A и любой signed bundle — блокировано TICKET-26.
- Audio-устройства (припаркованы в ADR-0003).
- QMK/VIA-платы — отдельная семья `origin: open_spec`, не через этот lane.

## Acceptance criteria

- [ ] Таблица `echo_rate` опубликована **до** любых выводов о реплаях; ни одна гипотеза не опирается на `EVIDENCE_VOID`
- [ ] Детектор эха сравнивает тело целиком; три регресс-теста зелёные
- [ ] Команды с пустым payload и эхо-ответом классифицированы `unknown`; **ни один блокер на них не построен**
- [ ] M HUB Web доведён до конфигуратора против fake-устройства минимум для одной модели каждого класса (HE-клавиатура, механика, мышь)
- [ ] Вывод вендорского IO-логгера **сверен** с собственным перехватом; расхождения записаны
- [ ] Все числовые поля: ≥ 4 точки sweep'а, исключающие более простую модель
- [ ] Для каждой record-команды захвачена запись **ровно одной** записи → stride
- [ ] Все enum'ы: сняты **все** значения
- [ ] Инвентарь UI-действий с непустым (или явно пустым и обоснованным) списком «найдено, не выполнено»
- [ ] `VendorKeyId` и `HidUsage` — разные типы; тест на несовпадение зелёный
- [ ] Layout извлечён **для каждого продукта**, ни один не унаследован
- [ ] Identity закрыт пассивно; fingerprint-числа перевыводятся из сырых дескрипторов отдельным инструментом в CI
- [ ] `hardware_shaped_hole` существует, машиночитаем, и каждая запись имеет `what_a_user_does_to_produce_one`
- [ ] Каждый блокер сформулирован через **факт**, и для каждого написано предложение «закроется, когда N пользователей выполнят X»
- [ ] Тест на блокеры утверждает точный список строк
- [ ] Проход 2' выполнен; в `hardware_shaped_hole` записано, что физического прохода не было

## Verification plan

Эмулятор, засеянный **реальными** кадрами вендорского прогона, включая несообразности (объявленная длина, не совпадающая с содержимым, — воспроизводится как есть; «прибирание» фикстуры = фальсификация доказательства, §4.2). Команда, которую никто не наблюдал, обязана **эхоить** в эмуляторе, а не выдавать правдоподобный ответ.

## TDD classification

REQUIRED для детектора эха, типов `VendorKeyId`/`HidUsage`, rank-блокеров и `hardware_shaped_hole`-эвалюатора. Съём данных — исследовательский.

## Expected architecture impact

Первая проверка `pdevemu`/`runtime.js` на семейной нейтральности. Ожидаемо всплывёт всё, что молча предполагает AULA: report id 9, 63-байтный payload, `.btn-connect` как селектор, `.q-tab` как признак конфигуратора. Каждое такое место — либо параметр профиля, либо баг.

---

## TICKET-25 FINAL A PREVIEW ELIGIBILITY PASS (2026-08-25)

Коммит `f5f98aa`. 132 теста зелёные. Real writes не выполнялись.

**Оговорка первой строкой:** точный текст v3 §58 (mandatory core surface) и
§42 (`PreviewEligible`) в этом репозитории и в worktree `aula-bytech` не
найден — есть только ссылки на номера в `VETRO_D_TO_A_PREVIEW_EXECUTION_PLAYBOOK_V4.md:37-38`.
Ниже использована операционная замена: словарь классов, которым весь этот
тикет уже пользовался (`SAFE_READ` / `POTENTIALLY_DESTRUCTIVE` /
`DESTRUCTIVE_CONFIRMED` / `UNKNOWN` / `BLOCKED`, правило «UNKNOWN никогда не
становится SAFE»), и покрытие core surface взято максимально широко — каждая
команда, реально наблюдённая в вендорском UI обеих семей (13 BY + 13 CZ),
а не заранее подобранное узкое подмножество.

**1. Mandatory core surface, по семьям.**

BY (`K99`, native Vue, report id 6, 519 байт):

| operation | evidence | read/write | reversible | destructive | rollback | provenance | uncertainty |
|---|---|---|---|---|---|---|---|
| identity/connect | vendor predicate + `getDevName`, static | — | — | no | n/a | vendor source, static | none |
| getBattery (`87`) | oracle, matches hpe template | read | — | no | n/a | vendor source + oracle | none |
| getPerformance (`84`) | oracle | read | — | no | n/a | vendor source + oracle | none |
| getKeySetting (`83`) | oracle | read | — | no | n/a | vendor source + oracle | none |
| getLightColor (`8a`) | oracle | read | — | no | n/a | vendor source + oracle | none |
| getDiyLight (`86`) | oracle | read | — | no | n/a | vendor source + oracle | none |
| setPerformance (`04`) | oracle, byte-identical to setReset (proof) | write | yes (`ToPreviousValue`, needs backup) | maybe | value-based | vendor source + oracle + proof | wire bytes carry no verdict — intent must |
| setReset (`04`) | oracle, captured via vendor dialog | write | no | yes | n/a (constant record) | vendor source + oracle + proof | none on the byte question; unresolved on enforcement |
| setKeySetting/setMacro/setDiyLight/setLightColor (writes `03`/`05`/`06`/`10`) | static only | write | unknown | unknown | unknown | vendor source (name only) | no captured frame, no known inverse |

CZ (`God 60`, cizhou iframe, report id 0, 64 bytes):

| operation | evidence | read/write | reversible | destructive | rollback | provenance | uncertainty |
|---|---|---|---|---|---|---|---|
| identity/connect | passive, 0 frames sent, 3 independent sources agree | — | — | no | n/a | vendor source, static+oracle | none |
| getInfo (`03`) / getBase (`04`) / getFuncConfig (`05`) / getKeyMatrix×2 (`07`/`08`) / `0c`/`a0`/`a9`/`f1` | oracle, `SYNTHETIC_FROM_VENDOR_SCHEMA` | **UNKNOWN by construction** | — | unknown | n/a | vendor source + oracle | byte 4 means "requested" for a read and "supplied" for a write; an all-zero trailing region proves nothing either way — structurally, not by missing effort |
| `06` (3 fields at offsets 9/20/35 located) | oracle + sweep, ≥4 points each | **WRITE** (non-zero trailing proves it) | unknown | yes (unconfirmed scope) | unknown | vendor source + oracle + sweep | which UI section owns the fields is not established |
| `0b` / `0d` / `f2` | oracle, non-zero trailing | **WRITE** | unknown | yes (unconfirmed scope) | unknown | vendor source + oracle | no field located, no known inverse |

**2. PreviewEligible per operation.**

| operation | verdict | reason (exactly one) |
|---|---|---|
| BY identity | PREVIEW_ELIGIBLE | static vendor predicate, no ambiguity, no side effect |
| BY getBattery/getPerformance/getKeySetting/getLightColor/getDiyLight | PREVIEW_ELIGIBLE (5) | confirmed `SAFE_READ`, serializer+reader cited from vendor source, oracle-observed |
| BY setPerformance | NOT_PREVIEW_ELIGIBLE | wire bytes can equal a factory reset (proof); safety now depends on intent provenance (`classify_by_intent`, this pass), and no production transport in this repo carries that provenance to the send call — the design is proven, the enforcement point does not exist yet |
| BY setReset | NOT_PREVIEW_ELIGIBLE | `DESTRUCTIVE_CONFIRMED` operations require a production confirmation gate (playbook §4.6: destructive generates no id through any door); no such gate exists for MCHOSE |
| BY setKeySetting/setMacro/setDiyLight/setLightColor (4) | UNKNOWN | no captured frame, no known inverse — cannot be assessed, not merely unsafe |
| CZ identity | PREVIEW_ELIGIBLE | passive, zero frames, three independent sources agree |
| CZ getInfo/getBase/getFuncConfig/getKeyMatrix×2/`0c`/`a0`/`a9`/`f1` (9) | NOT_PREVIEW_ELIGIBLE | CZ envelope cannot distinguish a read from a write of zeros at the wire level (byte 4 double-duty); no intent-provenance layer exists for CZ the way one now exists for BY |
| CZ `06`/`0b`/`0d`/`f2` (4) | NOT_PREVIEW_ELIGIBLE | classified `POTENTIALLY_DESTRUCTIVE`; full field scope unconfirmed and no CZ intent layer exists |

**3. Intent safety check.**

No production driver exists for MCHOSE anywhere in this repo or in `crates/`
— only the research harness (`protocol-miner/`). So "can the current
architecture preserve intent" splits in two:

- **As a design, proven this pass:** `classify_by_intent()`
  (`miner/dynamic/mchose_by_oracle.py`) takes the command name from the call
  site (`sendCommand('set', 'setPerformance', …)` vs
  `sendCommand('set', 'setReset', …)`) and returns different verdicts —
  `DESTRUCTIVE_CONFIRMED` vs `POTENTIALLY_DESTRUCTIVE` — for the *identical*
  519-byte frame. A frame offered with no command name is `BLOCKED`, not
  guessed. Six regression tests in
  `tests/test_mchose_by_intent_provenance.py`, including the required
  `same_wire_bytes` case and the opaque-replay `BLOCKED` case.
- **As a shipped enforcement point: does not exist.** Nothing in this repo
  calls `sendFeatureReport` for a real MCHOSE device. The proof says safety
  *can* survive to the transport boundary if the driver is written to keep
  the command name until the call; it does not say a driver does this today,
  because there is no driver.

**This is a real A Preview blocker, precisely because it is not
hardware-shaped:** it closes when someone writes the transport wrapper, not
when N users touch a keyboard.

**4. Critical contradictions.**

`critical_contradictions = 0` (after this pass; **2 found and fixed**, not
pre-existing-zero):

1. `BY_FAMILY.md` still asserted the withdrawn finding ("dialog opens only
   under undersized replies", `setReset` frames captured = 0, verdict
   `NOT_ESTABLISHED`) after `BY_0X04_INDISTINGUISHABLE.md` superseded it with
   the opposite, proven result. Fixed: section rewritten to point to the
   superseding document.
2. `BY_FAMILY.md` labelled wired lead byte `86` as `setDiyLight`.
   `static/kb_command_table.json`'s own `wired_leading_pair_groups["06 86"]`
   says `86` is `getDiyLight`'s read template; `setDiyLight`'s wired lead byte
   is `06`. Fixed with the artifact cited.

Also found and marked, not a contradiction: the top-of-file `Status`/`A
PREVIEW BLOCKER = OPEN` line was the *first* pass's snapshot, superseded by
every entry below it — annotated rather than deleted, since deleting history
would hide that the blocker was ever open at all.

**5. Coverage.**

```
mandatory_core_total = 26   (13 BY + 13 CZ, every command observed in the vendor's own UI)
mapped                = 26/26   (every one has a classification, none silently missing)
preview_eligible      = 7/26   (BY identity + 5 BY reads + CZ identity)
not_eligible          = 6/26   (BY setPerformance, setReset; CZ 4 proven writes)
unknown               = 13/26  (BY 4 unclassified writes; CZ 9 structurally UNKNOWN commands)
```

UI inventory coverage: **142/154** controls (CZ walk, capped and budgeted, kept
as its own axis, per instruction not merged into the count above).

3 unresolved marketing ids (`0x302d`/`0x3030`/`0x303e`): catalogue debt, not a
blocker — identity graph closes without them (`identity_graph.json`).

`SYNTHETIC_FROM_VENDOR_SCHEMA` stays exactly that: every CZ reply and every BY
reply this pass ever saw was harness-supplied. Nothing above is promoted to
hardware evidence, and per playbook §3 the AULA calibration gate (part 3),
required before the first signed bundle of *any* new family, was **not run
this pass** — its status is UNKNOWN, not passed, and that is named as a
blocker rather than assumed.

**6. Final verdict.**

| OPERATION | MAPPED | PREVIEW_ELIGIBLE | SAFETY CLASS | BLOCKER |
|---|---|---|---|---|
| BY identity | yes | yes | — | — |
| BY getBattery/getPerformance/getKeySetting/getLightColor/getDiyLight | yes | yes | SAFE_READ | — |
| BY setPerformance | yes | no | POTENTIALLY_DESTRUCTIVE (final) | no production transport carries intent to send |
| BY setReset | yes | no | DESTRUCTIVE_CONFIRMED | no production confirmation gate exists |
| BY setKeySetting/setMacro/setDiyLight/setLightColor | yes | unknown | UNKNOWN | no captured frame |
| CZ identity | yes | yes | — | — |
| CZ getInfo/getBase/getFuncConfig/getKeyMatrix×2/`0c`/`a0`/`a9`/`f1` | yes | no | UNKNOWN | envelope cannot prove direction; no CZ intent layer |
| CZ `06`/`0b`/`0d`/`f2` | yes | no | POTENTIALLY_DESTRUCTIVE | same — no CZ intent layer, scope unconfirmed |

```
MANDATORY CORE COVERAGE = 26/26 mapped, 7/26 Preview-eligible
PREVIEW ELIGIBLE = 7/26
CRITICAL CONTRADICTIONS = 0 (2 found and fixed this pass)
INTENT PROVENANCE SAFE = NO -- proven possible, not implemented (no MCHOSE driver exists in this repo)
```

**A PREVIEW PROGRESS = 60%**

Lower than the ~90% carried into this pass, and that drop is the actual
finding, not a mistake: prior passes tracked "is the BY wire-level
discriminator answered," which is now YES and final. This pass ran the full
§0.2 formula for the first time and found the mandatory-core-surface conjunct
was never checked against CZ at all. Once checked, CZ's core surface (every
command except identity) fails it for a structural reason, not a coverage
gap.

**A PREVIEW = BLOCKED.**

Minimal remaining blocker list (three; none hardware-shaped):

1. **No intent-provenance layer for CZ.** `classify_by_intent()` exists for
   BY; the same pattern (call-site command name, never wire bytes, for the
   ambiguous pair) has not been built for CZ's `06`/`0b`/`0d`/`f2` writes or
   for telling its nine UNKNOWN commands apart from writes-of-zero. Closes
   when that layer is written and tested — a code task.
2. **No production transport implementation exists for MCHOSE at all**, BY or
   CZ. `classify_by_intent` is proven correct but nothing calls it before a
   real `sendFeatureReport`/`sendReport`. Closes when a driver is written —
   a code task, not a hardware task.
3. **§58/§42 exact text and the playbook §3 calibration gate status are both
   unverified for MCHOSE.** Closes when the source document is located (or a
   project-approved substitute is adopted) and the gate is run once for the
   MCHOSE pipeline — a documentation/process task.

## Risks

- **M HUB может не пустить fake-устройство дальше опознания.** У AULA был прецедент: вкладка PERFORMANCE не рендерилась, пока не был захвачен `fetch_feature_advanced_key`, — фича гейтилась на capability-чтении, которого не было в корпусе. Здесь то же самое вероятно и лечится тем же способом: смотреть, на каком чтении застрял UI, и досеивать профиль.
- **Скоуп велик.** Три класса устройств (HE, механика, мыши) × десятки моделей. Тикет обязан начать с одной модели каждого класса и расширяться, а не пытаться снять всё сразу.
- **Соблазн объявить закрытым то, что проверено только эмулятором.** Эмулятор поймал ≠ плата согласилась. Всё, что выходит из этого тикета, — Preview, и запись в `hardware_shaped_hole` обязательна.
- **Вендорский логгер может усыпить бдительность.** Готовый лог трафика выглядит как готовое доказательство. Он им не является, пока не сверен.
