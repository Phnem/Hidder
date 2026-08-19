"""Desktop dynamic runtime manager using Frida instrumentation or simulated native process."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from miner.dynamic.desktop_hooks import generate_frida_script, normalize_desktop_trace_event


class FridaUnavailableError(RuntimeError):
    """Raised when Frida or required native injection tools are unavailable."""


class DesktopDynamicRunner:
    """Manages dynamic instrumentation of native desktop vendor utilities via Frida."""

    def __init__(
        self,
        script_code: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.script_code = script_code or generate_frida_script()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def is_available() -> bool:
        try:
            import frida  # type: ignore
            return True
        except ImportError:
            return False

    def run_session(
        self,
        target_executable_or_pid: str | int,
        actions_callback: Callable[[], None] | None = None,
        simulated_events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Attach to / spawn target executable, inject HID hooks, capture events, and normalize."""
        # Safe test fixture path if simulated_events provided or test harness
        if simulated_events is not None:
            normalized = []
            for ev in simulated_events:
                normalized.append(normalize_desktop_trace_event(ev))
            if actions_callback is not None:
                actions_callback()
            return normalized

        try:
            import frida  # type: ignore
        except ImportError as exc:
            raise FridaUnavailableError("frida Python package is not installed in the environment") from exc

        recorded_raw: list[dict[str, Any]] = []

        def on_message(message: dict[str, Any], data: Any) -> None:
            if message.get("type") == "send":
                payload = message.get("payload")
                if isinstance(payload, dict):
                    recorded_raw.append(payload)

        try:
            if isinstance(target_executable_or_pid, int):
                session = frida.attach(target_executable_or_pid)
                spawned = False
                pid = target_executable_or_pid
            else:
                pid = frida.spawn([str(target_executable_or_pid)])
                session = frida.attach(pid)
                spawned = True

            script = session.create_script(self.script_code)
            script.on("message", on_message)
            script.load()

            if spawned:
                frida.resume(pid)

            if actions_callback is not None:
                actions_callback()
            else:
                time.sleep(min(self.timeout_seconds, 2.0))

            script.unload()
            session.detach()

            if spawned:
                try:
                    frida.kill(pid)
                except Exception:
                    pass
        except FridaUnavailableError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Desktop Frida dynamic capture failed: {exc}") from exc

        return [normalize_desktop_trace_event(ev) for ev in recorded_raw]

    def run_and_save_trace(
        self,
        target: str | int,
        output_trace_path: Path,
        actions_callback: Callable[[], None] | None = None,
        simulated_events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute session and write normalized JSONL trace."""
        traces = self.run_session(target, actions_callback, simulated_events)
        output_trace_path.parent.mkdir(parents=True, exist_ok=True)
        with output_trace_path.open("w", encoding="utf-8") as f:
            for item in traces:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return traces
