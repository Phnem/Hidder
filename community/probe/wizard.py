"""Interactive guided research wizard for community users.

Designed for non-technical users with clear Russian UI, 5-minute workflows,
support for WebHID browser configurators and Native desktop software,
proper action duration windows, and zero-hardware-writing safety invariants.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from probe.hid_discovery import DiscoveredHidCandidate, enumerate_hid_devices, is_generic_driver_string
    from probe.observer import PassiveTransportObserver
    from probe.privacy import PrivacyScrubber
    from probe.schema import (
        CaptureMetadata,
        CommunityObservationBundle,
        DeviceIdentity,
        GuidedAction,
        QualityScore,
        VendorSoftwareInfo,
    )
except ImportError:
    from community.probe.hid_discovery import DiscoveredHidCandidate, enumerate_hid_devices, is_generic_driver_string
    from community.probe.observer import PassiveTransportObserver
    from community.probe.privacy import PrivacyScrubber
    from community.probe.schema import (
        CaptureMetadata,
        CommunityObservationBundle,
        DeviceIdentity,
        GuidedAction,
        QualityScore,
        VendorSoftwareInfo,
    )


class CommunityResearchWizard:
    """Orchestrates the 5-minute guided observation session."""

    def __init__(self, is_demo: bool = False, output_dir: Path | None = None) -> None:
        self.is_demo = is_demo
        self.output_dir = output_dir or Path.cwd()
        self.scrubber = PrivacyScrubber()
        
        self.category: str = "keyboard"
        self.keyboard_type: str | None = "mechanical"
        self.user_reported_model: str = ""
        self.selected_device: DiscoveredHidCandidate | None = None
        self.vendor_process_name: str = ""
        self.guided_actions: list[GuidedAction] = []
        self.completed: bool = False
        self.started_at: str = ""
        
        self.observer = PassiveTransportObserver(
            target_vid=self.selected_device.vid if self.selected_device else "",
            target_pid=self.selected_device.pid if self.selected_device else ""
        )

    def run(self) -> Path:
        """Run the full guided workflow and export the JSON bundle."""
        self.started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        try:
            self._print_banner()
            self._step_category_selection()
            self._step_model_name_input()
            self._step_device_discovery()
            self._step_physical_input_baseline()
            self._step_vendor_software_detection()
            self._step_idle_baseline()
            self._step_guided_vendor_experiments()
            self.completed = True
        except KeyboardInterrupt:
            print("\n\n[!] Сессия прервана пользователем. Сохранение частичного результата...")
            self.completed = False
        except Exception as exc:
            print(f"\n\n[!] Ошибка: {exc}. Сохранение частичного результата...")
            self.completed = False

        output_path = self._export_bundle()
        if self.completed:
            self._print_final_screen(output_path)
        else:
            print(f"\nЧастичный лог сохранён в: {output_path.name}")
        return output_path

    # --- UI Helpers ---

    def _print_banner(self) -> None:
        print("====================================================")
        print("           Peripheral Research Probe                ")
        print("====================================================")
        print("Спасибо за участие в исследовании устройств!")
        print("\nЭтот тест:")
        print(" • Займёт около 5 минут")
        print(" • Ничего не устанавливает и не прошивает")
        print(" • Не собирает личные данные и историю набора")
        print(" • Создаёт ровно один итоговый JSON-файл")
        print("====================================================\n")

    def _prompt_choice(self, prompt: str, options: list[str], default: int = 1) -> int:
        print(prompt)
        for idx, opt in enumerate(options, 1):
            print(f" [{idx}] {opt}")
        while True:
            if self.is_demo:
                print(f"> Выбрано (demo): [{default}] {options[default-1]}")
                return default
            try:
                raw = input(f"> [по умолчанию {default}]: ").strip()
                if not raw:
                    return default
                val = int(raw)
                if 1 <= val <= len(options):
                    return val
                print(f"Пожалуйста, введите число от 1 до {len(options)}")
            except ValueError:
                print("Пожалуйста, введите корректный номер.")

    # --- Steps ---

    def _step_category_selection(self) -> None:
        choice = self._prompt_choice(
            "Выберите тип вашего устройства:",
            ["Клавиатура (Keyboard)", "Мышь (Mouse)"],
            default=1
        )
        if choice == 1:
            self.category = "keyboard"
            type_choice = self._prompt_choice(
                "\nКакой тип клавиатуры?",
                [
                    "Механическая клавиатура (Mechanical)",
                    "Магнитная клавиатура / Hall Effect (Rapid Trigger)",
                    "Не уверен / Обычная"
                ],
                default=1
            )
            if type_choice == 1:
                self.keyboard_type = "mechanical"
            elif type_choice == 2:
                self.keyboard_type = "hall_effect"
            else:
                self.keyboard_type = "unknown"
        else:
            self.category = "mouse"
            self.keyboard_type = None

    def _step_model_name_input(self) -> None:
        print("\n----------------------------------------------------")
        print("Укажите модель устройства")
        print("----------------------------------------------------")
        print("Например: AULA F75, AULA HERO 84 HE, Attack Shark X68, ATK F1, Logitech G502")
        if self.is_demo:
            self.user_reported_model = "Demo Device 2026"
            print(f"> Модель (demo): {self.user_reported_model}")
            return

        while True:
            raw = input("> Введите название модели: ").strip()
            if raw:
                self.user_reported_model = self.scrubber.scrub_text(raw)
                break
            print("Пожалуйста, введите название модели устройства.")

    def _step_device_discovery(self) -> None:
        print("\n[i] Поиск подключенных HID-устройств...")
        candidates = enumerate_hid_devices(self.category)
        if not candidates:
            candidates = enumerate_hid_devices()

        if self.user_reported_model:
            model_tokens = [t.lower() for t in re.split(r"[\s\-_]+", self.user_reported_model) if len(t) > 1]
            def _rank_score(cand: DiscoveredHidCandidate) -> int:
                score = 0
                combined = f"{cand.display_name} {cand.manufacturer} {cand.product_string}".lower()
                for tok in model_tokens:
                    if tok in combined:
                        score += 10
                if cand.category == self.category:
                    score += 5
                return score
            candidates = sorted(candidates, key=_rank_score, reverse=True)

        options = []
        for idx, c in enumerate(candidates):
            rec_tag = " (Рекомендуемое)" if idx == 0 and self.user_reported_model else ""
            options.append(f"{c.display_name} [{c.vid}:{c.pid}]{rec_tag}")
        options.append("Другое устройство / Ввести вручную")

        choice = self._prompt_choice(
            f"\nНайдено {len(candidates)} устройств. Выберите ваше:",
            options,
            default=1
        )
        if choice <= len(candidates):
            self.selected_device = candidates[choice - 1]
        else:
            self.selected_device = DiscoveredHidCandidate(
                display_name=self.user_reported_model,
                vid="0x372E",
                pid="0x103E",
                manufacturer="Generic",
                category=self.category,
                usage_page=0x01,
                usage=0x06 if self.category == "keyboard" else 0x02,
                device_path=f"HID\\{self.user_reported_model}",
            )
        print(f"[+] Выбрано: {self.selected_device.display_name} ({self.selected_device.vid}:{self.selected_device.pid})")
        self.observer.target_vid = self.selected_device.vid
        self.observer.target_pid = self.selected_device.pid

    def _step_physical_input_baseline(self) -> None:
        if self.category == "keyboard":
            self._guided_action(
                action_id="phys_keys_wasd",
                category="input_baseline",
                title="Шаг 1 — Базовые клавиши",
                instruction="Нажмите медленно по очереди клавиши: W, A, S, D, затем Пробел и Enter.",
                semantic={"setting": "input.keys", "keys": ["W", "A", "S", "D", "Space", "Enter"]}
            )
            
            if self.keyboard_type == "hall_effect":
                print("\n[i] Базовые аналоговые нажатия для магнитной клавиатуры:")
                self._guided_action(
                    action_id="he_w_light",
                    category="analog_baseline",
                    title="Шаг 1.1 — Лёгкое нажатие",
                    instruction="Нажмите клавишу W ОЧЕНЬ СЛАБО (10-20% хода, не до конца) и отпустите.",
                    semantic={"setting": "he.analog.travel", "depth": "light"}
                )
                self._guided_action(
                    action_id="he_w_half",
                    category="analog_baseline",
                    title="Шаг 1.2 — Нажатие наполовину",
                    instruction="Нажмите клавишу W примерно НАПОЛОВИНУ хода и отпустите.",
                    semantic={"setting": "he.analog.travel", "depth": "half"}
                )
                self._guided_action(
                    action_id="he_w_full",
                    category="analog_baseline",
                    title="Шаг 1.3 — Полное нажатие",
                    instruction="Нажмите клавишу W ДО УПОРА (100% хода) и отпустите.",
                    semantic={"setting": "he.analog.travel", "depth": "full"}
                )
                self._guided_action(
                    action_id="he_w_slow",
                    category="analog_baseline",
                    title="Шаг 1.4 — Плавное нажатие",
                    instruction="Плавно нажимайте клавишу W сверху вниз в течение 2 секунд, затем плавно отпустите.",
                    semantic={"setting": "he.analog.travel", "depth": "smooth_ramp"}
                )
        else:
            self._guided_action(
                action_id="mouse_phys_buttons",
                category="input_baseline",
                title="Шаг 1 — Базовые кнопки мыши",
                instruction="Нажмите: ЛКМ, ПКМ, СКМ, колесо вверх и вниз.",
                semantic={"setting": "mouse.input", "buttons": ["LMB", "RMB", "MMB", "Scroll"]}
            )

    def _step_vendor_software_detection(self) -> None:
        print("\n----------------------------------------------------")
        print("Шаг 2 — Тип программы для настройки устройства")
        print("----------------------------------------------------")
        print("Как настраивается ваше устройство?")
        
        sw_choice = self._prompt_choice(
            "",
            [
                "Веб-конфигуратор (в браузере — AULA WebHub, Keychron Launcher, DrunkDeer, Wooting и др.)",
                "Установленная программа Windows (десктопная программа — Bloody, Armoury Crate, iCUE, Hub.exe и др.)",
                "Пропустить привязку программы"
            ],
            default=1
        )

        if sw_choice == 1:
            self._setup_webhid_browser()
        elif sw_choice == 2:
            self._setup_native_desktop()
        else:
            print("[-] Наблюдение за софтом пропущено. Будет сохранен только сценарий действий.")

    def _setup_webhid_browser(self) -> None:
        print("\n[i] Настройка изолированного браузера с перехватом WebHID...")
        if self.is_demo:
            self.vendor_process_name = "msedge.exe"
            self.observer.attach_webhid("https://example.com")
            return

        target_url = "about:blank"
        raw_url = input("> Введите адрес веб-конфигуратора (или нажмите Enter для открытия браузера): ").strip()
        if raw_url:
            if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
                raw_url = f"https://{raw_url}"
            target_url = raw_url

        print(f"[i] Запуск браузера (Edge / Chrome) с чистым временным профилем...")
        ok = self.observer.attach_webhid(target_url)
        if ok:
            b_name = self.observer.capture_metadata.browser or "browser"
            self.vendor_process_name = f"{b_name}.exe"
            print(f"[✓] Браузер успешно запущен ({b_name}). Перехватчик WebHID активирован!")
            print("    В открывшемся окне браузера откройте конфигуратор и подключите устройство.")
            input("\n> Нажмите [Enter], когда устройство подключено в браузере: ")
        else:
            print("[!] Не удалось запустить браузер с инструментарием CDP.")

    def _setup_native_desktop(self) -> None:
        print("\n1. Откройте официальную программу устройства на ПК.")
        print("2. Пока ничего не меняйте в настройках.")
        print("3. Вернитесь сюда и нажмите Enter.")
        if not self.is_demo:
            input("\n> Нажмите [Enter], когда программа открыта: ")

        detected_proc, detected_pid = self._find_and_select_vendor_process()
        if detected_proc and detected_pid:
            self.vendor_process_name = detected_proc
            print(f"[+] Выбран процесс: {self.vendor_process_name} (PID: {detected_pid})")
            try:
                ok = self.observer.attach_native(detected_pid, self.vendor_process_name)
                if ok:
                    print("[✓] Перехватчик успешно подключен к программе устройства.")
                else:
                    print("[!] Не удалось подключиться к процессу.")
            except PermissionError:
                print("\n[!] ВНИМАНИЕ: Программа запущена от имени Администратора.")
                print("    Перезапустите PeripheralResearch от имени Администратора.")
            except Exception as e:
                print(f"[!] Ошибка подключения: {e}")

    def _find_and_select_vendor_process(self) -> tuple[str, int | None]:
        if self.is_demo:
            return "OfficialVendorHub.exe", 1234
            
        try:
            cmd = [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-Process | Where-Object { $_.MainWindowTitle -ne '' -or $_.Name -match 'aula|atk|hero|vgn|bloody|keychron|akko|attackshark|epomaker|darkproject|corsair|logitech|razer|hyperx|cougar|drunkdeer' } | "
                "Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json -Depth 2"
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=5)
            if proc.returncode == 0 and proc.stdout:
                stdout_str = proc.stdout.decode("utf-8", errors="replace")
                data = json.loads(stdout_str)
                if isinstance(data, dict):
                    data = [data]
                    
                known_candidates = []
                for item in data:
                    pid = item.get("Id")
                    pname = str(item.get("ProcessName") or "")
                    wtitle = str(item.get("MainWindowTitle") or "")
                    
                    if pname.lower() in ("explorer", "powershell", "cmd", "conhost", "taskmgr", "code", "idle"):
                        continue
                        
                    label = f"{pname}.exe (PID: {pid})"
                    if wtitle:
                        label += f" — \"{wtitle}\""
                    known_candidates.append((f"{pname}.exe", pid, label))
                    
                if known_candidates:
                    print("\nОбнаружены запущенные программы. Выберите программу вашего устройства:")
                    opts = [c[2] for c in known_candidates]
                    opts.append("Другой процесс / Пропустить подключение")
                    
                    c_idx = self._prompt_choice("", opts, default=1)
                    if c_idx <= len(known_candidates):
                        sel = known_candidates[c_idx - 1]
                        return sel[0], sel[1]
        except Exception:
            pass
            
        return "", None

    def _step_idle_baseline(self) -> None:
        print("\n[i] Снятие фонового трафика (3 секунды, не трогайте настройки)...")
        self.observer.start_idle_baseline()
        if self.is_demo:
            for _ in range(3):
                self.observer.record_event("sendFeatureReport", "feature_out", 0, "000000000000", self.vendor_process_name or "browser.exe")
        time.sleep(1.0 if self.is_demo else 3.0)
        self.observer.stop_idle_baseline()
        if len(self.observer.idle_baseline_events) > 0:
            print(f"[+] Фоновый трафик зафиксирован ({len(self.observer.idle_baseline_events)} пакетов).")
        else:
            print("[i] Фоновый трафик отсутствует (устройство находится в состоянии покоя).")

    def _step_guided_vendor_experiments(self) -> None:
        if self.category == "keyboard" and self.keyboard_type == "hall_effect":
            self._guided_he_keyboard_tests()
        elif self.category == "keyboard":
            self._guided_mechanical_keyboard_tests()
        else:
            self._guided_mouse_tests()

    def _guided_mechanical_keyboard_tests(self) -> None:
        self._test_setting_with_restore(
            action_id="light_effect",
            title="Тест K1 — Эффект подсветки",
            instruction_change="В программе устройства переключите РЕЖИМ/ЭФФЕКТ подсветки на ЛЮБОЙ ДРУГОЙ.",
            instruction_restore="Теперь верните предыдущий эффект подсветки обратно.",
            semantic={"setting": "light.effect"}
        )
        self._test_setting_with_restore(
            action_id="light_brightness",
            title="Тест K2 — Яркость подсветки",
            instruction_change="Измените ЯРКОСТЬ подсветки (например, установите около 50%).",
            instruction_restore="Верните яркость в исходное положение.",
            semantic={"setting": "light.brightness"}
        )
        self._test_setting_with_restore(
            action_id="kb_polling",
            title="Тест K3 — Частота опроса (Polling Rate)",
            instruction_change="Измените частоту опроса (например, 1000 Гц → 500 Гц).",
            instruction_restore="Верните исходную частоту опроса.",
            semantic={"setting": "kb.polling"}
        )
        self._test_setting_with_restore(
            action_id="kb_keymap",
            title="Тест K4 — Переназначение клавиши",
            instruction_change="Временно переназначьте Caps Lock на клавишу Escape и примените.",
            instruction_restore="Верните Caps Lock обратно.",
            semantic={"setting": "kb.keymap"}
        )

    def _guided_he_keyboard_tests(self) -> None:
        # HE1: Actuation
        self._test_setting_with_restore(
            action_id="he_actuation",
            title="Тест HE1 — Точка срабатывания (Actuation Point)",
            instruction_change="Для клавиши W измените точку срабатывания (например, с 0.51 мм на 0.75 мм).",
            instruction_restore="Верните исходную точку срабатывания для клавиши W (0.51 мм).",
            semantic={"setting": "he.actuation"}
        )
        # HE2: Rapid Trigger Toggle
        self._test_setting_with_restore(
            action_id="he_rt_toggle",
            title="Тест HE2 — Переключатель Rapid Trigger",
            instruction_change="Включите или выключите переключатель Rapid Trigger (RT).",
            instruction_restore="Верните переключатель Rapid Trigger в исходное состояние.",
            semantic={"setting": "he.rt.enabled"}
        )
        # HE3: RT Press
        self._test_setting_with_restore(
            action_id="he_rt_press",
            title="Тест HE3 — Чувствительность нажатия RT (Press)",
            instruction_change="Измените чувствительность нажатия RT Press (например, на 0.2 мм).",
            instruction_restore="Верните чувствительность RT Press обратно.",
            semantic={"setting": "he.rt.press"}
        )
        # HE4: RT Release
        self._test_setting_with_restore(
            action_id="he_rt_release",
            title="Тест HE4 — Чувствительность отпускания RT (Release)",
            instruction_change="Измените чувствительность отпускания RT Release (например, на 0.2 мм).",
            instruction_restore="Верните чувствительность RT Release обратно.",
            semantic={"setting": "he.rt.release"}
        )
        # HE5: RGB Effect
        self._test_setting_with_restore(
            action_id="light_effect",
            title="Тест HE5 — Эффект подсветки",
            instruction_change="Переключите режим подсветки на другой.",
            instruction_restore="Верните исходный режим подсветки.",
            semantic={"setting": "light.effect"}
        )

    def _guided_mouse_tests(self) -> None:
        self._test_setting_with_restore(
            action_id="mouse_dpi",
            title="Тест M1 — Разрешение сенсора (DPI)",
            instruction_change="Переключите активный DPI на другое значение (например, 800 → 1600).",
            instruction_restore="Верните исходное значение DPI.",
            semantic={"setting": "mouse.dpi"}
        )
        self._test_setting_with_restore(
            action_id="mouse_polling",
            title="Тест M2 — Частота опроса мыши",
            instruction_change="Измените частоту опроса (например, 1000 Гц → 500 Гц или 4000/8000 Гц).",
            instruction_restore="Верните исходную частоту опроса.",
            semantic={"setting": "mouse.polling"}
        )
        self._test_setting_with_restore(
            action_id="mouse_lod",
            title="Тест M3 — Высота отрыва (LOD)",
            instruction_change="Измените высоту отрыва LOD (например, Low → High или 1 мм → 2 мм).",
            instruction_restore="Верните исходную высоту отрыва.",
            semantic={"setting": "mouse.lod"}
        )
        self._test_setting_with_restore(
            action_id="mouse_debounce",
            title="Тест M4 — Задержка дребезга (Debounce)",
            instruction_change="Измените задержку debounce (например, 4 мс → 8 мс).",
            instruction_restore="Верните исходное значение debounce.",
            semantic={"setting": "mouse.debounce"}
        )
        self._test_setting_with_restore(
            action_id="mouse_rgb",
            title="Тест M5 — Подсветка мыши",
            instruction_change="Измените режим или цвет подсветки.",
            instruction_restore="Верните исходную подсветку.",
            semantic={"setting": "light.effect"}
        )

    def _test_setting_with_restore(
        self,
        action_id: str,
        title: str,
        instruction_change: str,
        instruction_restore: str,
        semantic: dict[str, Any],
    ) -> None:
        res = self._guided_action(
            action_id=f"{action_id}_change",
            category="vendor_experiment",
            title=f"{title} (Шаг 1: Изменение)",
            instruction=instruction_change,
            semantic=semantic
        )
        if res == "skip":
            return

        self._guided_action(
            action_id=f"{action_id}_restore",
            category="vendor_restore",
            title=f"{title} (Шаг 2: Возврат в исходное состояние)",
            instruction=instruction_restore,
            semantic={**semantic, "restore": True},
            restore_attempted=True
        )

    def _guided_action(
        self,
        action_id: str,
        category: str,
        title: str,
        instruction: str,
        semantic: dict[str, Any],
        restore_attempted: bool = False,
    ) -> str:
        print(f"\n----------------------------------------------------")
        print(f"{title}")
        print(f"----------------------------------------------------")
        print(f"Инструкция: {instruction}")
        print("\n[ENTER] — Начать запись действия")
        print("[S]     — Пропустить этот шаг")
        print("[Q]     — Завершить исследование и сохранить результат")
        
        if self.is_demo:
            user_in = "enter"
        else:
            user_in = input("> ").strip().lower()
            if user_in in ("s", "skip"):
                print("[~] Шаг пропущен.")
                self.guided_actions.append(GuidedAction(
                    action_id=action_id,
                    category=category,
                    instruction=instruction,
                    expected_semantic=semantic,
                    started_at=time.time(),
                    finished_at=time.time(),
                    status="skipped_by_user",
                ))
                return "skip"
            elif user_in in ("q", "quit", "exit"):
                raise KeyboardInterrupt()

        # START ACTION WINDOW
        t_start = time.time()
        self.observer.set_active_action(action_id)
        
        print("\n[● ИДЁТ ЗАПИСЬ...]")
        print("1. Перейдите в программу/веб-конфигуратор и выполните действие.")
        print("2. Подождите около 2 секунд.")
        print("3. Вернитесь сюда и нажмите [ENTER] для завершения шага.")
        
        if self.is_demo:
            time.sleep(0.1)
            self.observer.record_event(
                api="sendFeatureReport",
                direction="feature_out",
                report_id=9 if "he" in action_id else 5,
                bytes_hex="0913001e004b00740000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000" if "change" in action_id else "0913001e0033008c0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
                process_basename=self.vendor_process_name or "browser.exe"
            )
        else:
            try:
                input("\n> Нажмите [Enter], когда действие выполнено: ")
            except KeyboardInterrupt:
                pass

        # FINISH ACTION WINDOW
        t_end = time.time()
        self.observer.set_active_action(None)
        
        duration = t_end - t_start
        print(f"[✓] Действие зафиксировано (окно: {duration:.1f} сек).")
        
        self.guided_actions.append(GuidedAction(
            action_id=action_id,
            category=category,
            instruction=instruction,
            expected_semantic=semantic,
            started_at=t_start,
            finished_at=t_end,
            duration_seconds=round(duration, 3),
            status="completed",
            restore_attempted=restore_attempted,
        ))
        return "enter"

    # --- Quality Scoring & Export ---

    def _calculate_quality(self) -> QualityScore:
        completed_actions = [a for a in self.guided_actions if a.status == "completed"]
        restore_actions = [a for a in completed_actions if a.restore_attempted]
        input_actions = [a for a in completed_actions if a.category == "input_baseline"]
        analog_actions = [a for a in completed_actions if a.category == "analog_baseline"]
        
        traffic_seen = len(self.observer.observations) > 0
        idle_seen = len(self.observer.idle_baseline_events) > 0
        device_bound = self.selected_device is not None
        vendor_bound = self.observer.capture_metadata.observer_attached
        
        if not traffic_seen:
            score = 15 if completed_actions else 5
            rating = "Guided actions only — no protocol traffic"
        else:
            score = 30
            if device_bound:
                score += 20
            if vendor_bound:
                score += 15
            if idle_seen:
                score += 15
            if len(restore_actions) > 0:
                score += 10
            if len(analog_actions) > 0 or len(input_actions) > 0:
                score += 10
                
            score = min(100, max(0, score))
            if score >= 80:
                rating = "Excellent protocol evidence"
            elif score >= 60:
                rating = "Strong change/restore capture"
            elif score >= 40:
                rating = "Useful device-bound capture"
            else:
                rating = "Transport capture, weak binding"
            
        return QualityScore(
            score=score,
            rating=rating,
            device_bound=device_bound,
            vendor_process_bound=vendor_bound,
            traffic_observed=traffic_seen,
            idle_baseline_captured=idle_seen,
            change_restore_pairs_count=len(restore_actions),
            known_input_actions_count=len(input_actions),
            analog_actions_count=len(analog_actions),
        )

    def _export_bundle(self) -> Path:
        finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        correlations = self.observer.correlate_actions(self.guided_actions)
        quality = self._calculate_quality()
        
        dev_id = self.selected_device
        
        # Resolve model identity safely (never generic strings)
        raw_product = dev_id.product_string if dev_id else ""
        if raw_product and not is_generic_driver_string(raw_product):
            resolved_model = raw_product
            resolved_confidence = "registry_verified"
        else:
            resolved_model = None
            resolved_confidence = "unresolved"

        device_data = DeviceIdentity(
            category=self.category,
            user_reported_model=self.user_reported_model,
            detected_product_string=raw_product if not is_generic_driver_string(raw_product) else "",
            detected_manufacturer_string=dev_id.manufacturer if dev_id else "",
            resolved_model=resolved_model,
            resolved_model_confidence=resolved_confidence,
            keyboard_type=self.keyboard_type,
            vid=dev_id.vid if dev_id else "0x0000",
            pid=dev_id.pid if dev_id else "0x0000",
        )
        
        software_data = VendorSoftwareInfo(
            process_basename=self.scrubber.sanitize_path(self.vendor_process_name) if self.vendor_process_name else ""
        )
        
        capture_meta = self.observer.capture_metadata
        capture_meta.ended_at = finished_at
        
        bundle = CommunityObservationBundle(
            submission_id=f"comm-{uuid.uuid4().hex[:12]}",
            started_at=self.started_at,
            finished_at=finished_at,
            completed=self.completed,
            is_demo=self.is_demo,
            device=device_data,
            software=software_data,
            capture=capture_meta,
            guided_actions=self.guided_actions,
            transport_observations=self.observer.observations,
            correlations=correlations,
            quality=quality,
            privacy_scrubbed=True,
        )
        bundle.payload_sha256 = bundle.compute_sha256()
        
        ts_slug = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        vid_slug = device_data.vid.replace("0x", "").upper()
        pid_slug = device_data.pid.replace("0x", "").upper()
        cat_slug = self.category
        if self.keyboard_type == "hall_effect":
            cat_slug = "keyboard-he"
            
        status_suffix = "" if self.completed else "-partial"
        filename = f"PeripheralResearch-{cat_slug}-{vid_slug}-{pid_slug}-{ts_slug}{status_suffix}.json"
        
        output_file = self.output_dir / filename
        sanitized_dict = self.scrubber.sanitize_dict(bundle.to_dict())
        
        output_file.write_text(
            json.dumps(sanitized_dict, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8"
        )
        return output_file

    def _print_final_screen(self, output_path: Path) -> None:
        traffic_seen = len(self.observer.observations) > 0
        
        print("\n====================================================")
        print("                    Готово!                         ")
        print("====================================================")
        print("Лог исследования успешно сохранён:")
        print(f"👉 {output_path.name}\n")
        
        if traffic_seen:
            print(f"[✓] Записано {len(self.observer.observations)} пакетов протокола.")
            print("Огромное спасибо! Вы помогаете добавить поддержку вашего устройства в Peripheral ❤️\n")
        else:
            print("[!] Внимание: технический трафик программы устройства не был зафиксирован.")
            print("    Лог сохранён, но содержит только сценарий ваших действий.")

        print("Отправить полученный JSON-файл можно автору:")
        print(" Telegram:")
        print(" https://t.me/Phnem_pro\n")
        print("В файл сохранены исключительно технические данные устройства.")
        print("Личные данные, текст который вы печатаете, и серийные номера")
        print("НЕ сохраняются.")
        print("====================================================")
        if not self.is_demo:
            try:
                input("\nНажмите Enter для выхода...")
            except Exception:
                pass
