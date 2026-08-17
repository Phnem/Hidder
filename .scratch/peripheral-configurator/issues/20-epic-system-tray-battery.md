# TICKET-20 (EPIC): System Tray + `dev.power.*`/`dev.connection` capability

## Status

PENDING — split into two independently-startable tracks (see Scope); tray shell is not blocked by Phase 1, battery data-path is.

## Objective

Добавлено 2026-08-17 по итогам code review плана. Соответствует предложению: единая иконка в системном трее (Tauri 2 `TrayIcon`, Windows/macOS/Linux) с quick-панелью подключённых устройств, динамическим отображением заряда (рендер в памяти, не набор PNG), и генерализованная capability `dev.power.battery`/`dev.power.charging`/`dev.connection` (заменяет `mouse.battery` — заряд относится к любому wireless-устройству, не только к мыши).

## User or system value

Постоянно видимый статус периферии без открытия полного окна приложения — то, чего вендорские Electron-драйверы почти никогда не делают прилично. Низкая инженерная сложность (Tauri даёт `TrayIcon` из коробки) относительно продуктовой ценности.

## Dependencies

- **Tray shell (иконка, quick-панель, click-поведение, Settings → Tray):** TICKET-13 (UI skeleton уже закладывает три IPC-канала — tray использует Commands+Events, не требует Channels). Может стартовать сразу после TICKET-13, независимо от Phase 2+.
- **Реальные данные `dev.power.battery`:** первый protocol engine с wireless-семейством. По текущему hardware-треку (AULA + ROYUAN, оба USB-only — см. `spec.md` доменное правило Wireless) это упирается в TICKET-19 (мыши, Phase 6) либо в более раннее появление wireless-клавиатуры вне выбранных семейств. До этого момента tray работает на моковом/пустом `BatteryState`.

## Scope (эпик-уровень; декомпозиция на архитектурном чекпоинте перед стартом)

- `dev.power.battery` (u8, %), `dev.power.charging` (bool), `dev.connection` (enum `usb`/`wireless_2_4ghz`/`bluetooth`) в `pcaps` — генерализация бывшего `mouse.battery` (см. `spec.md` Приложение A, изменение от 2026-08-17).
- `ProtocolEngine`/`ReceiverMouse` реализации возвращают единый `BatteryState { percent, charging }` независимо от происхождения (стандартный HID `Battery Strength` usage / вендорский `GET_BATTERY` опкод / `GET_DEVICE_STATUS` через ресивер) — доменное правило уже зафиксировано в `spec.md`.
- Tray shell: `TrayIcon` через Tauri 2, quick-панель (список устройств, имя, `dev.connection`, заряд если применимо), кнопки "Profiles"/"Open".
- Settings → Tray: режим иконки (app icon / lowest battery / выбранное устройство / hide), click behavior (quick panel / открыть окно), пороги предупреждений о низком заряде (дефолт 20%/10%, SAFE_DEFAULT).
- In-memory рендеринг иконки (percent → RGBA-буфер 16/20/32px) в `app`-слое, не в core-крейтах (см. `architecture/INITIAL_REVIEW.md` §8).
- Уведомления о низком заряде (Tauri notification / native OS notification, если доступно без доп. разрешений).

## Out of scope

- Полноценная батарейная телеметрия (напряжение, история разряда) — `dev.power.voltage`/`dev.power.low` из открытого списка плана §17.9 остаются DEFERRED, не в этом эпике.
- Процесс-watcher для игровых пресетов (§17.9 плана) — смежная, но отдельная фича; см. флаг реконсиляции в `spec.md` SAFE_DEFAULT о фоновом tray-процессе.

## Acceptance criteria (эпик-уровень)

- [ ] Tray-иконка отображается на всех трёх ОС через единый Tauri API (без платформенно-специфичного кода в `app`, кроме того, что сам Tauri абстрагирует).
- [ ] Quick-панель показывает реальные подключённые устройства (минимум `dev.connection`; заряд — как только доступен реальный wireless-engine).
- [ ] Логика "иконка = наименьший заряд" корректно работает на N устройствах и на 0 устройствах (тест уже зафиксирован в `spec.md` Test seams).
- [ ] Рендеринг иконки не хранит наборы PNG в бинаре — генерируется в памяти.
- [ ] `dev.power.battery` возвращает `Verified(hw)`-значение хотя бы для одного реального wireless-устройства (гейтится TICKET-19 или его аналогом).

## Verification plan

Tray shell — ручное тестирование на трёх ОС (клик, quick-панель, смена состояния подключения). Battery-selection логика — unit-тест (Test seams в `spec.md`). Полный сквозной путь (реальный заряд реального устройства → иконка) — hardware-in-the-loop, гейтится наличием wireless-устройства.

## TDD classification

Смешанно: battery-selection logic и `BatteryState`-агрегация — REQUIRED (детерминированная логика); tray UI/иконки — NOT_NEEDED/RECOMMENDED. Финализируется при декомпозиции.

## Expected architecture impact

Расширяет `pcaps` двумя новыми capability-группами (`dev.power.*`, `dev.connection`), не меняя существующие HE/keymap capability. Добавляет presentation-only ответственность в `app`-слой (иконка-рендеринг) — не затрагивает core crate DAG (§9 architecture review).

## Risks

- Без wireless-устройства в реальном тест-парке battery-путь верифицируется только на моках до Phase 6 — явно зафиксировано в Dependencies, не скрыто.
- Пересечение с процесс-watcher'ом (§17.9 плана) может потребовать пересмотра архитектуры фонового tray-процесса — см. флаг в `spec.md`.

## Implementation notes

Empty before implementation. Декомпозиция на вертикальные тикеты (tray shell отдельно от battery data-path) — на архитектурном чекпоинте, вероятно после TICKET-13 для shell-трека и после TICKET-19 (или более раннего wireless-устройства) для data-path трека.

## Deviations

Empty before implementation.

## Review findings

Empty before review.

## Completion evidence

Empty before completion.
