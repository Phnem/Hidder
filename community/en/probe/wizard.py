"""Interactive guided research wizard for community users (English edition).

Designed for non-technical users with clear English UI, 5-minute workflows,
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
    from community.en.probe.hid_discovery import DiscoveredHidCandidate, enumerate_hid_devices, is_generic_driver_string
    from community.en.probe.observer import PassiveTransportObserver
    from community.en.probe.privacy import PrivacyScrubber
    from community.en.probe.schema import (
        CaptureMetadata,
        CommunityObservationBundle,
        DeviceIdentity,
        GuidedAction,
        QualityScore,
        VendorSoftwareInfo,
    )


class CommunityResearchWizard:
    """Orchestrates the 5-minute guided observation session in English."""

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
        
        self.observer = PassiveTransportObserver()

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
            print("\n\n[!] Session interrupted by user. Saving partial result...")
            self.completed = False
        except Exception as exc:
            print(f"\n\n[!] Error: {exc}. Saving partial result...")
            self.completed = False

        output_path = self._export_bundle()
        if self.completed:
            self._print_final_screen(output_path)
        else:
            print(f"\nPartial log saved to: {output_path.name}")
        return output_path

    # --- UI Helpers ---

    def _print_banner(self) -> None:
        print("====================================================")
        print("           Peripheral Research Probe                ")
        print("====================================================")
        print("Thank you for helping improve device support!")
        print("\nThis test:")
        print(" • Takes about 5 minutes")
        print(" • Does not install or flash anything")
        print(" • Does not collect personal typing or passwords")
        print(" • Creates exactly one final JSON file")
        print("====================================================\n")

    def _prompt_choice(self, prompt: str, options: list[str], default: int = 1) -> int:
        if prompt:
            print(prompt)
        for idx, opt in enumerate(options, 1):
            print(f" [{idx}] {opt}")
        while True:
            if self.is_demo:
                print(f"> Selected (demo): [{default}] {options[default-1]}")
                return default
            try:
                raw = input(f"> [default {default}]: ").strip()
                if not raw:
                    return default
                val = int(raw)
                if 1 <= val <= len(options):
                    return val
                print(f"Please enter a number between 1 and {len(options)}")
            except ValueError:
                print("Please enter a valid number.")

    # --- Steps ---

    def _step_category_selection(self) -> None:
        choice = self._prompt_choice(
            "Select your device category:",
            ["Keyboard", "Mouse"],
            default=1
        )
        if choice == 1:
            self.category = "keyboard"
            type_choice = self._prompt_choice(
                "\nWhat type of keyboard is it?",
                [
                    "Mechanical keyboard",
                    "Magnetic / Hall Effect keyboard (Rapid Trigger)",
                    "Not sure / Standard"
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
        print("Device Model Name")
        print("----------------------------------------------------")
        print("Examples: AULA F75, AULA HERO 84 HE, Attack Shark X68, ATK F1, Logitech G502")
        if self.is_demo:
            self.user_reported_model = "AULA HERO 84 HE"
            print(f"> Model (demo): {self.user_reported_model}")
            return

        while True:
            raw = input("> Enter model name: ").strip()
            if raw:
                self.user_reported_model = self.scrubber.scrub_text(raw)
                break
            print("Please enter the device model name.")

    def _step_device_discovery(self) -> None:
        print("\n[i] Scanning for connected HID devices...")
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
            rec_tag = " (Recommended)" if idx == 0 and self.user_reported_model else ""
            options.append(f"{c.display_name} [{c.vid}:{c.pid}]{rec_tag}")
        options.append("Other device / Enter manually")

        choice = self._prompt_choice(
            f"\nFound {len(candidates)} device(s). Select yours:",
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
        print(f"[+] Selected: {self.selected_device.display_name} ({self.selected_device.vid}:{self.selected_device.pid})")
        self.observer.target_vid = self.selected_device.vid.upper().replace("0X", "")
        self.observer.target_pid = self.selected_device.pid.upper().replace("0X", "")

    def _step_physical_input_baseline(self) -> None:
        if self.category == "keyboard":
            self._guided_action(
                action_id="phys_keys_wasd",
                category="input_baseline",
                title="Step 1 — Basic Key Inputs",
                instruction="Press these keys slowly one by one: W, A, S, D, then Space and Enter.",
                semantic={"setting": "input.keys", "keys": ["W", "A", "S", "D", "Space", "Enter"]}
            )
            
            if self.keyboard_type == "hall_effect":
                print("\n[i] Hall Effect Magnetic Key Travel Baseline:")
                self._guided_action(
                    action_id="he_w_light",
                    category="analog_baseline",
                    title="Step 1.1 — Light Key Travel",
                    instruction="Press key W VERY LIGHTLY (10-20% travel, without bottoming out) and release.",
                    semantic={"setting": "he.analog.travel", "depth": "light"}
                )
                self._guided_action(
                    action_id="he_w_half",
                    category="analog_baseline",
                    title="Step 1.2 — Half Key Travel",
                    instruction="Press key W about halfway down and release.",
                    semantic={"setting": "he.analog.travel", "depth": "half"}
                )
                self._guided_action(
                    action_id="he_w_full",
                    category="analog_baseline",
                    title="Step 1.3 — Full Bottom-out Travel",
                    instruction="Press key W all the way down (bottom out) and release.",
                    semantic={"setting": "he.analog.travel", "depth": "full"}
                )
                self._guided_action(
                    action_id="he_w_slow",
                    category="analog_baseline",
                    title="Step 1.4 — Smooth Analog Ramp",
                    instruction="Slowly press key W from top to bottom over ~2 seconds and slowly release it.",
                    semantic={"setting": "he.analog.travel", "depth": "smooth_ramp"}
                )
        else:
            self._guided_action(
                action_id="mouse_phys_buttons",
                category="input_baseline",
                title="Step 1 — Basic Mouse Buttons",
                instruction="Perform: Left Click, Right Click, Middle Click, scroll Wheel Up and Down.",
                semantic={"setting": "mouse.input", "buttons": ["LMB", "RMB", "MMB", "Scroll"]}
            )

    def _step_vendor_software_detection(self) -> None:
        print("\n----------------------------------------------------")
        print("Step 2 — Device Configurator Type")
        print("----------------------------------------------------")
        print("How do you configure your device?")
        
        sw_choice = self._prompt_choice(
            "",
            [
                "Web Configurator (in browser — AULA WebHub, Keychron Launcher, DrunkDeer, Wooting, etc.)",
                "Installed Windows App (desktop app — Bloody, Armoury Crate, iCUE, Hub.exe, etc.)",
                "Skip software observation"
            ],
            default=1
        )

        if sw_choice == 1:
            self._setup_webhid_browser()
        elif sw_choice == 2:
            self._setup_native_desktop()
        else:
            print("[-] Software observation skipped. Action script only will be saved.")

    def _setup_webhid_browser(self) -> None:
        print("\n[i] Launching isolated browser with WebHID instrumentation...")
        if self.is_demo:
            self.vendor_process_name = "msedge.exe"
            self.observer.attach_webhid("https://example.com")
            return

        target_url = "about:blank"
        raw_url = input("> Enter web configurator URL (or press Enter to open browser): ").strip()
        if raw_url:
            if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
                raw_url = f"https://{raw_url}"
            target_url = raw_url

        print(f"[i] Launching browser (Edge / Chrome) with clean temporary profile...")
        ok = self.observer.attach_webhid(target_url)
        if ok:
            b_name = self.observer.capture_metadata.browser or "browser"
            self.vendor_process_name = f"{b_name}.exe"
            print(f"[✓] Browser launched ({b_name}). WebHID observer active!")
            print("    In the browser window, open your configurator and connect your device.")
            input("\n> Press [Enter] once device is connected in the browser: ")
        else:
            print("[!] Could not launch browser with CDP instrumentation.")

    def _setup_native_desktop(self) -> None:
        print("\n1. Open the official device software on your PC.")
        print("2. Do not change any settings yet.")
        print("3. Return here and press Enter.")
        if not self.is_demo:
            input("\n> Press [Enter] once software is open: ")

        detected_proc, detected_pid = self._find_and_select_vendor_process()
        if detected_proc and detected_pid:
            self.vendor_process_name = detected_proc
            print(f"\n====================================================")
            print("        Connect to Vendor Software                  ")
            print("====================================================")
            print(f"Selected application: {self.vendor_process_name} (PID: {detected_pid})")
            print("\nTo record technical HID commands, Hidder will temporarily attach")
            print("to the official vendor application.")
            print("\nHidder:")
            print(" • Does NOT modify application files")
            print(" • Does NOT record passwords or typed text")
            print(" • Detaches immediately after the test completes")
            print("\n[Enter] — Continue")
            print("[S]     — Skip software connection")
            
            if not self.is_demo:
                choice = input("\n> ").strip().lower()
                if choice in ("s", "skip"):
                    print("[-] Software connection skipped by user.")
                    return

            try:
                ok = self.observer.attach_native(detected_pid, self.vendor_process_name)
                if ok:
                    print("[✓] Observer attached to vendor software.")
                else:
                    print("[!] Could not attach to process.")
            except PermissionError:
                print("\n[!] WARNING: Software is running as Administrator.")
                print("    Please restart Hidder as Administrator.")
            except Exception as e:
                print(f"[!] Attach error: {e}")

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
                    print("\nRunning software detected. Select your device application:")
                    opts = [c[2] for c in known_candidates]
                    opts.append("Other process / Skip attach")
                    
                    c_idx = self._prompt_choice("", opts, default=1)
                    if c_idx <= len(known_candidates):
                        sel = known_candidates[c_idx - 1]
                        return sel[0], sel[1]
        except Exception:
            pass
            
        return "", None

    def _step_idle_baseline(self) -> None:
        print("\n----------------------------------------------------")
        print("Step 3 — Idle Baseline Traffic Capture")
        print("----------------------------------------------------")
        print("Please do not touch or change settings for 3 seconds...")
        self.observer.start_idle_baseline()
        if self.is_demo:
            for _ in range(3):
                self.observer.record_event(
                    api="sendFeatureReport",
                    direction="feature_out",
                    report_id=0,
                    bytes_hex="000000000000",
                    process_basename=self.vendor_process_name or "browser.exe"
                )
        time.sleep(0.5 if self.is_demo else 3.0)
        self.observer.stop_idle_baseline()
        if len(self.observer.idle_baseline_events) > 0:
            print(f"[+] Idle baseline recorded ({len(self.observer.idle_baseline_events)} packets).")
        else:
            print("[i] No idle traffic observed (device is in quiescent state).")

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
            title="Test K1 — RGB Lighting Effect",
            instruction_change="In the software, change the RGB LIGHTING EFFECT to ANY OTHER MODE.",
            instruction_restore="Now restore the previous lighting effect back.",
            semantic={"setting": "light.effect"}
        )
        self._test_setting_with_restore(
            action_id="light_brightness",
            title="Test K2 — RGB Brightness",
            instruction_change="Change the RGB BRIGHTNESS (e.g., set to around 50%).",
            instruction_restore="Restore brightness to its original level.",
            semantic={"setting": "light.brightness"}
        )
        self._test_setting_with_restore(
            action_id="kb_polling",
            title="Test K3 — Polling Rate",
            instruction_change="Change the polling rate (e.g., 1000 Hz → 500 Hz).",
            instruction_restore="Restore the original polling rate.",
            semantic={"setting": "kb.polling"}
        )
        self._test_setting_with_restore(
            action_id="kb_keymap",
            title="Test K4 — Key Remapping",
            instruction_change="Temporarily remap Caps Lock to Escape and apply.",
            instruction_restore="Restore Caps Lock back to normal.",
            semantic={"setting": "kb.keymap"}
        )

    def _guided_he_keyboard_tests(self) -> None:
        # HE1: Actuation
        self._test_setting_with_restore(
            action_id="he_actuation",
            title="Test HE1 — Actuation Point",
            instruction_change="For key W, change the actuation point (e.g., from 0.51 mm to 0.75 mm).",
            instruction_restore="Restore the original actuation point for key W (0.51 mm).",
            semantic={"setting": "he.actuation"}
        )
        # HE2: Rapid Trigger Toggle
        self._test_setting_with_restore(
            action_id="he_rt_toggle",
            title="Test HE2 — Rapid Trigger Toggle",
            instruction_change="Toggle the Rapid Trigger (RT) switch ON or OFF.",
            instruction_restore="Restore the Rapid Trigger switch to its original state.",
            semantic={"setting": "he.rt.enabled"}
        )
        # HE3: RT Press
        self._test_setting_with_restore(
            action_id="he_rt_press",
            title="Test HE3 — Rapid Trigger Press Sensitivity",
            instruction_change="Change the RT Press sensitivity (e.g., to 0.2 mm).",
            instruction_restore="Restore the RT Press sensitivity.",
            semantic={"setting": "he.rt.press"}
        )
        # HE4: RT Release
        self._test_setting_with_restore(
            action_id="he_rt_release",
            title="Test HE4 — Rapid Trigger Release Sensitivity",
            instruction_change="Change the RT Release sensitivity (e.g., to 0.2 mm).",
            instruction_restore="Restore the RT Release sensitivity.",
            semantic={"setting": "he.rt.release"}
        )
        # HE5: RGB Effect
        self._test_setting_with_restore(
            action_id="light_effect",
            title="Test HE5 — RGB Lighting Effect",
            instruction_change="Change the RGB lighting effect to a different mode.",
            instruction_restore="Restore the original lighting effect.",
            semantic={"setting": "light.effect"}
        )

    def _guided_mouse_tests(self) -> None:
        self._test_setting_with_restore(
            action_id="mouse_dpi",
            title="Test M1 — Sensor DPI",
            instruction_change="Change active DPI to another value (e.g., 800 → 1600).",
            instruction_restore="Restore the original DPI value.",
            semantic={"setting": "mouse.dpi"}
        )
        self._test_setting_with_restore(
            action_id="mouse_polling",
            title="Test M2 — Mouse Polling Rate",
            instruction_change="Change polling rate (e.g., 1000 Hz → 500 Hz or 4000/8000 Hz).",
            instruction_restore="Restore the original polling rate.",
            semantic={"setting": "mouse.polling"}
        )
        self._test_setting_with_restore(
            action_id="mouse_lod",
            title="Test M3 — Lift-Off Distance (LOD)",
            instruction_change="Change sensor LOD (e.g., Low → High or 1 mm → 2 mm).",
            instruction_restore="Restore original LOD.",
            semantic={"setting": "mouse.lod"}
        )
        self._test_setting_with_restore(
            action_id="mouse_debounce",
            title="Test M4 — Button Debounce Time",
            instruction_change="Change debounce delay (e.g., 4 ms → 8 ms).",
            instruction_restore="Restore original debounce value.",
            semantic={"setting": "mouse.debounce"}
        )
        self._test_setting_with_restore(
            action_id="mouse_rgb",
            title="Test M5 — Mouse Lighting",
            instruction_change="Change the mouse lighting effect/color.",
            instruction_restore="Restore original lighting.",
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
            title=f"{title} (Step 1: Change)",
            instruction=instruction_change,
            semantic=semantic
        )
        if res == "skip":
            return

        self._guided_action(
            action_id=f"{action_id}_restore",
            category="vendor_restore",
            title=f"{title} (Step 2: Restore Original)",
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
        print(f"Instruction: {instruction}")
        print("\n[ENTER] — Start recording action")
        print("[S]     — Skip this step")
        print("[Q]     — Quit research and save results")
        
        if self.is_demo:
            user_in = "enter"
        else:
            user_in = input("> ").strip().lower()
            if user_in in ("s", "skip"):
                print("[~] Step skipped.")
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
        
        print("\n[● RECORDING ACTIVE...]")
        print("1. Switch to the vendor application/web-configurator and perform the change.")
        print("2. Wait about 2 seconds.")
        print("3. Return here and press [ENTER] to finish the step.")
        
        if self.is_demo:
            time.sleep(0.05)
            self.observer.record_event(
                api="sendFeatureReport",
                direction="feature_out",
                report_id=9 if "he" in action_id else 5,
                bytes_hex="0913001e004b00740000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000" if "change" in action_id else "0913001e0033008c0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
                process_basename=self.vendor_process_name or "browser.exe"
            )
        else:
            try:
                input("\n> Press [Enter] once the change is performed: ")
            except KeyboardInterrupt:
                pass

        # FINISH ACTION WINDOW
        t_end = time.time()
        self.observer.set_active_action(None)
        
        duration = t_end - t_start
        print(f"[✓] Action recorded (window: {duration:.1f}s).")
        
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
        
        correlations = self.observer.correlate_actions(self.guided_actions)
        paired_corrs = [c for c in correlations if c.restore_action_id is not None and c.changed_offsets]
        has_mirrored_restore = any(c.restore_matches_original for c in paired_corrs)
        
        if not traffic_seen:
            score = 15 if completed_actions else 5
            rating = "Guided actions only — no protocol traffic"
        else:
            score = 25
            if device_bound:
                score += 20
            if vendor_bound:
                score += 15
            if idle_seen:
                score += 15
            if len(paired_corrs) > 0:
                score += min(15, len(paired_corrs) * 5)
            if has_mirrored_restore:
                score += 10
            if len(analog_actions) > 0 or len(input_actions) > 0:
                score += 5
                
            if score >= 80:
                if not (traffic_seen and device_bound and len(paired_corrs) > 0):
                    score = 75
            if score >= 90 and not idle_seen:
                score = 85
                
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
            change_restore_pairs_count=len(paired_corrs),
            known_input_actions_count=len(input_actions),
            analog_actions_count=len(analog_actions),
        )

    def _export_bundle(self) -> Path:
        finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        correlations = self.observer.correlate_actions(self.guided_actions)
        quality = self._calculate_quality()
        
        dev_id = self.selected_device
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
        print("                     Done!                          ")
        print("====================================================")
        print("The research log was saved successfully:")
        print(f"👉 {output_path.name}\n")
        
        if traffic_seen:
            print(f"[✓] Recorded {len(self.observer.observations)} protocol packets.")
            print("Thank you very much! You are helping Peripheral support more devices ❤️\n")
        else:
            print("[!] Warning: Technical protocol traffic could not be captured.")
            print("    The log was saved with your action script only.\n")

        print("You can send the resulting JSON file to the developer:")
        print(" Telegram:")
        print(" https://t.me/Phnem_pro\n")
        print("The file contains technical device data only.")
        print("Personal text, keystroke history, and USB serial numbers")
        print("are NOT saved.")
        print("====================================================")
        if not self.is_demo:
            try:
                input("\nPress Enter to exit...")
            except Exception:
                pass
