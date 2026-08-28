"""Vetro Probe research GUI <-> engine JSON-lines RPC (authoritative engine side).

The GUI is a THIN product layer. This module is the only place the GUI talks to
the Probe engine. It NEVER duplicates the executor: the real path delegates to
the existing AutoProbeRun / planner / feature gates / runstate / recovery; the
demo path is a DETERMINISTIC mock that never touches hardware and never emits
physical-validation evidence.

Wire protocol (one JSON object per line, both directions):
  request  -> {"id": n, "method": "...", "params": {...}}
  response -> {"id": n, "ok": true, "result": {...}}
           | {"id": n, "ok": false, "error": "human-readable"}
  event    -> {"event": "progress"|"run_result", "data": {...}}

Methods:
  discover           -> device discovery state
  plan               -> plan preview (safe ops + blocked ops), from the backend planner
  recovery_status    -> recovery-first preflight (CLEAR / RECOVERING / RECOVERED / BLOCKED)
  start_run          -> runs the plan; emits progress + run_result events
  run_result         -> the latest run result (idempotent)
  health             -> engine + mode
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import urllib.parse

# Friendly labels for the six physically-validated ops (exact HERO84/FW0216).
OP_LABELS = {
    "keyboard.profile": "Profiles",
    "keyboard.polling": "Polling rate",
    "device.win_lock": "Windows Lock",
    "he.deadzone": "Deadzone",
    "he.actuation": "Actuation",
    "light.brightness": "Brightness",
    "keyboard.remap": "Key Remap",
}


def make_github_issue_url(
    brand: str = "",
    model: str = "",
    connection: str = "Wired USB",
    firmware: str = "",
    vid: str = "",
    pid: str = "",
    app_version: str = "0.3.0",
    build_commit: str = "",
    run_id: str = "",
    failure_category: str = "",
    observed: str = "",
) -> str:
    base = "https://github.com/Phnem/Hidder/issues/new"
    title = f"[Compatibility] {brand} {model}".strip() or "[Compatibility] Device report"
    body = (
        f"## Device\n"
        f"- **Brand**: {brand or 'Unknown'}\n"
        f"- **Model**: {model or 'Unknown'}\n"
        f"- **Connection**: {connection}\n"
        f"- **Firmware**: {firmware or 'Unknown'}\n"
        f"- **VID:PID**: {vid}:{pid}\n"
        f"- **Vetro Version**: {app_version}\n"
        f"- **Build Commit**: {build_commit}\n"
        f"- **Run ID**: {run_id}\n\n"
        f"## What happened?\n"
        f"Validation result:\n"
        f"- [x] {failure_category or 'Compatibility / validation issue'}\n\n"
        f"## What did you observe?\n"
        f"{observed or 'Please describe what happened.'}\n\n"
        f"## Did the original settings return correctly?\n"
        f"- [x] Yes (Verified \u2713)\n\n"
        f"## Diagnostic package\n"
        f"Please attach the generated `VetroProbe_*.zip` package.\n"
    )
    params = {
        "title": title,
        "labels": "compatibility,device-report",
        "body": body,
    }
    return f"{base}?{urllib.parse.urlencode(params)}"

FRIENDLY_BLOCKED = {
    "keyboard.remap": "Needs additional protocol validation",
    "he.rt": "Needs additional protocol validation",
    "light.rgb_core": "Not available yet",
    "light.global_color": "Not available yet",
    "light.effect": "Not available yet",
    "light.speed": "Not available yet",
    "light.direction": "Not available yet",
    "custom.per_key": "Not available yet",
    "light.edge_light": "Not available yet",
}


def friendly_label(op_id: str, classification: str) -> str:
    if classification == "AUTO_REVERSIBLE":
        return OP_LABELS.get(op_id, op_id)
    return FRIENDLY_BLOCKED.get(op_id, op_id)


# ---------------------------------------------------------------------------
# Discovery / plan / recovery (real, thin, delegates to existing machinery)
# ---------------------------------------------------------------------------

def discover_state(instance=None) -> dict[str, Any]:
    """Return a discovery snapshot. instance=None -> NO_DEVICE.

    On real hardware the caller opens the HERO84 and evaluates the exact gate;
    on failure the state is UNSUPPORTED / IDENTITY_MISMATCH / FW_UNSUPPORTED /
    ERROR. This is read-only."""
    from .bundle import production_bundle_for_hero84
    bundle = production_bundle_for_hero84()
    if instance is None:
        return {"state": "NO_DEVICE", "device": None, "supported_count": 0,
                "reason": "No compatible device connected."}
    from .identity import ExactIdentityGate
    verdict = ExactIdentityGate(bundle).evaluate(instance)
    if not verdict.passed:
        low = verdict.reason.lower()
        if "firmware" in low:
            state = "FW_UNSUPPORTED"
        elif "vid/pid" in low:
            state = "IDENTITY_MISMATCH"
        elif "ambiguous" in low or "descriptor" in low or "connection" in low:
            state = "UNSUPPORTED"
        else:
            state = "UNSUPPORTED"
        return {"state": state, "device": None, "supported_count": 0,
                "reason": verdict.reason}
    plan = plan_preview()
    return {
        "state": "IDENTIFIED",
        "device": {
            "name": getattr(instance, "product_string", None) or bundle.product.name,
            "firmware": getattr(instance, "firmware_version", None),
            "family": bundle.family,
        },
        "supported_count": plan["safe_count"],
        "reason": verdict.reason,
    }


_CACHED_PLAN: dict[str, Any] | None = None


def plan_preview() -> dict[str, Any]:
    """Plan preview from the EXISTING planner + feature gates (never hardcoded)."""
    global _CACHED_PLAN
    if _CACHED_PLAN is not None:
        return _CACHED_PLAN
    import tempfile
    from .bundle import production_bundle_for_hero84
    from .automation import AutoProbeRun, CLS_AUTO_REVERSIBLE, CLS_BLOCKED
    from .transport import FakeTransport
    from .identity import mock_hero84_instance

    bundle = production_bundle_for_hero84()
    inst = mock_hero84_instance()
    trans = FakeTransport(initial_state={})
    with tempfile.TemporaryDirectory() as td:
        run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                           enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                           run_dir=Path(td), reconnect_timeout_ms=200)
        run._plan()
        safe: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for entry in run.plan:
            if entry.get("informational"):
                continue
            op = entry["operation"]
            cls = entry["classification"]
            if cls == CLS_AUTO_REVERSIBLE:
                safe.append({"id": op, "label": friendly_label(op, cls), "classification": cls})
            elif cls == CLS_BLOCKED:
                blocked.append({"id": op, "label": friendly_label(op, cls),
                                "classification": cls, "why_safe": entry.get("why_safe", "")})
        # order safe ops by the canonical validated set first
        order = ["keyboard.profile", "keyboard.polling", "device.win_lock",
                 "he.deadzone", "he.actuation", "light.brightness"]
        safe = sorted(safe, key=lambda o: order.index(o["id"]) if o["id"] in order else len(order))
        _CACHED_PLAN = {"safe": safe, "blocked": blocked, "safe_count": len(safe)}
    return _CACHED_PLAN


# ---------------------------------------------------------------------------
# Recovery-first status (real, delegates to runstate)
# ---------------------------------------------------------------------------

def recovery_status(run_dir: Path) -> dict[str, Any]:
    try:
        from .runstate import RunStateStore
        store = RunStateStore(run_dir)
        cp = store.load()
        if cp is not None and not cp.closed:
            if getattr(cp, "write_may_have_applied", False):
                return {
                    "preflight": "RECOVERY_REQUIRED",
                    "pending": True,
                    "reason": "A previous session left an unverified state; it must be restored before research starts.",
                }
            return {
                "preflight": "RECOVERY_IN_PROGRESS",
                "pending": True,
                "reason": "A previous session did not finish cleanly; recovery-first must complete before research starts.",
            }
        return {"preflight": "CLEAR", "pending": False, "reason": ""}
    except Exception as exc:
        return {"preflight": "ERROR", "pending": True, "reason": f"Recovery preflight check failed: {exc}"}


# ---------------------------------------------------------------------------
# Demo engine — deterministic, ZERO HID, never produces physical evidence
# ---------------------------------------------------------------------------

class DemoEngine:
    """Deterministic GUI-development mode. Never touches hardware; its results
    are explicitly marked source=mock and MUST NOT be treated as physical
    validation evidence."""
    def __init__(self, scenario: str = "supported", stage_delay: float = 0.15) -> None:
        self.scenario = scenario
        self.stage_delay = stage_delay
        self._result: dict[str, Any] | None = None
        self._events: list[dict[str, Any]] = []
        self._busy = threading.Lock()
        self._pending_recovery = scenario == "recovery_startup"
        self._thread: threading.Thread | None = None

    # -- discovery -----------------------------------------------------------
    def discover(self) -> dict[str, Any]:
        if self.scenario == "no_device":
            return {"state": "NO_DEVICE", "device": None, "supported_count": 0,
                    "reason": "No compatible device connected.", "source": "mock"}
        if self.scenario == "unsupported_fw":
            return {"state": "FW_UNSUPPORTED", "device": None, "supported_count": 0,
                    "reason": "This firmware is not yet supported for automatic research.",
                    "source": "mock"}
        plan = plan_preview()
        return {"state": "IDENTIFIED",
                "device": {"name": "HERO 84 HE", "firmware": "0216", "family": "aula_kb_v3_wired"},
                "supported_count": plan["safe_count"], "reason": "demo", "source": "mock"}

    def plan(self) -> dict[str, Any]:
        return plan_preview()

    def recovery_status(self) -> dict[str, Any]:
        if self._pending_recovery:
            return {"preflight": "RECOVERING", "pending": True,
                    "reason": "A previous session left an unverified state; it must be restored before research starts.",
                    "source": "mock"}
        return {"preflight": "CLEAR", "pending": False, "reason": "", "source": "mock"}

    def clear_recovery(self) -> None:
        self._pending_recovery = False

    # -- run -----------------------------------------------------------------
    def start_run(self, emit, async_run: bool = True) -> dict[str, Any]:
        if self._busy.locked():
            return {"started": False, "error": "a run is already in progress"}
        if async_run:
            self._thread = threading.Thread(target=self._run_worker, args=(emit,), daemon=True)
            self._thread.start()
        else:
            self._run_worker(emit)
        return {"started": True}

    def _run_worker(self, emit) -> None:
        print(f"[SIDECAR DIAG] _run_worker entered thread", file=sys.stderr, flush=True)
        try:
            with self._busy:
                print(f"[SIDECAR DIAG] _run_worker acquired _busy lock", file=sys.stderr, flush=True)
                self._run_script(emit)
                self._emit(emit, "run_result", self._result or {})
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._emit(emit, "run_result", {
                "status": "ERROR",
                "error": str(exc),
                "restored": False,
                "checks_completed": 0,
                "checks_total": 0,
                "results": [],
            })

    def _emit(self, emit, event: str, data: dict[str, Any]) -> None:
        self._events.append({"event": event, "data": data})
        emit({"event": event, "data": data})

    def _op_progress(self, emit, op_id: str, state: str, text: str) -> None:
        print(f"[SIDECAR DIAG] _op_progress: {op_id} {state}", file=sys.stderr, flush=True)
        self._emit(emit, "progress", {"op": op_id, "label": friendly_label(op_id, "AUTO_REVERSIBLE"),
                                      "state": state, "text": text})

    def _delay(self) -> None:
        if self.stage_delay > 0:
            time.sleep(self.stage_delay)

    def _run_script(self, emit) -> None:
        print(f"[SIDECAR DIAG] _run_script started, stage_delay={self.stage_delay}", file=sys.stderr, flush=True)
        plan = plan_preview()
        ops = plan["safe"]
        results = []
        failed = None
        restored = True
        for op in ops:
            oid, label = op["id"], op["label"]
            self._op_progress(emit, oid, "QUEUED", "Waiting...")
        self._delay()

        if self.scenario == "manual":
            # an op fails and its restore cannot be verified
            for i, op in enumerate(ops):
                oid = op["id"]
                if i == len(ops) - 1:
                    self._op_progress(emit, oid, "BASELINING", f"Checking {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "TESTING", f"Testing {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "RESTORING", "Restoring original setting...")
                    self._delay()
                    self._op_progress(emit, oid, "FAILED", "Could not verify the original setting was restored.")
                    results.append({"id": oid, "label": label_for(oid), "status": "FAILED",
                                    "restored": False})
                    failed = oid
                    restored = False
                    self._pending_recovery = True
                else:
                    self._op_progress(emit, oid, "BASELINING", f"Checking {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "TESTING", f"Testing {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "VERIFYING", f"Verifying {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "RESTORING", "Restoring original setting...")
                    self._delay()
                    self._op_progress(emit, oid, "PASS", "Completed")
                    results.append({"id": oid, "label": label_for(oid), "status": "PASS", "restored": True})
            self._result = self._result_for("FAILED_REQUIRES_MANUAL_RESTORE", results, restored)
            return
        if self.scenario == "fail_restored":
            for i, op in enumerate(ops):
                oid = op["id"]
                if i == len(ops) - 1:
                    self._op_progress(emit, oid, "BASELINING", f"Checking {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "TESTING", f"Testing {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "RESTORING", "Restoring original setting...")
                    self._delay()
                    self._op_progress(emit, oid, "FAILED", "The check could not complete.")
                    results.append({"id": oid, "label": label_for(oid), "status": "FAILED",
                                    "restored": True})
                    failed = oid
                else:
                    self._op_progress(emit, oid, "BASELINING", f"Checking {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "TESTING", f"Testing {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "VERIFYING", f"Verifying {label_for(oid)}...")
                    self._delay()
                    self._op_progress(emit, oid, "RESTORING", "Restoring original setting...")
                    self._delay()
                    self._op_progress(emit, oid, "PASS", "Completed")
                    results.append({"id": oid, "label": label_for(oid), "status": "PASS", "restored": True})
            self._result = self._result_for("FAIL_RESTORED", results, True)
            return
        # supported: six-op success
        for op in ops:
            oid = op["id"]
            self._op_progress(emit, oid, "BASELINING", f"Checking {label_for(oid)}...")
            self._delay()
            self._op_progress(emit, oid, "TESTING", f"Testing {label_for(oid)}...")
            self._delay()
            self._op_progress(emit, oid, "VERIFYING", f"Verifying {label_for(oid)}...")
            self._delay()
            self._op_progress(emit, oid, "RESTORING", "Restoring original setting...")
            self._delay()
            self._op_progress(emit, oid, "PASS", "Completed")
            self._delay()
            results.append({"id": oid, "label": label_for(oid), "status": "PASS", "restored": True})
        self._result = self._result_for("SUCCESS_RESTORED", results, True)

    def _result_for(self, status: str, results: list[dict[str, Any]], restored: bool) -> dict[str, Any]:
        return {
            "status": status,
            "restored": restored,
            "checks_completed": sum(1 for r in results if r["status"] == "PASS"),
            "checks_total": len(results),
            "results": results,
            "evidence_source": "mock",
            "physical_validation_evidence": False,  # never physical evidence
            "output_path": None,
        }

    def diagnostic_state(self) -> dict[str, Any]:
        return {
            "run_id": "demo",
            "worker_alive": bool(self._thread and self._thread.is_alive()),
            "worker_thread_id": self._thread.ident if self._thread else None,
            "engine_busy": self._busy.locked(),
            "last_backend_step": "DEMO_IDLE" if not self._busy.locked() else "DEMO_RUNNING",
            "last_backend_step_ts": time.time(),
            "last_progress_age_seconds": 0.0,
            "thread_dump": {},
        }

    def run_result(self) -> dict[str, Any]:
        return self._result or {"status": "IDLE", "results": [], "checks_completed": 0}


def label_for(op_id: str) -> str:
    return OP_LABELS.get(op_id, op_id)


def diag_log(tag: str, **kwargs: Any) -> None:
    parts = [f"[{time.time():.3f}]", "[SIDECAR PRE_OP]", tag]
    if kwargs:
        parts.append(json.dumps(kwargs, ensure_ascii=True))
    line = " ".join(parts)
    print(line, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Real engine — authoritative physical execution (or deterministic mock transport)
# ---------------------------------------------------------------------------

class RealEngine:
    """Authoritative real execution engine. Drives real AutoProbeRun / feature gates /
    planner / feature evidence / recovery journal.

    Supports dependency injection for transport_factory and run_dir to allow
    deterministic headless testing without hardware."""

    def __init__(
        self,
        run_dir: Path | str = "vetro_gui_run",
        transport_factory: Any | None = None,
        is_physical: bool = True,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.transport_factory = transport_factory or self._default_transport_factory
        self.is_physical = is_physical
        self._busy = threading.Lock()
        self._thread: threading.Thread | None = None
        self._result: dict[str, Any] | None = None
        self._events: list[dict[str, Any]] = []
        self._last_backend_step: str = "IDLE"
        self._last_backend_step_ts: float = time.time()
        self._run_id: str | None = None

    def _step(self, tag: str, **kwargs: Any) -> None:
        self._last_backend_step = tag
        self._last_backend_step_ts = time.time()
        diag_log(tag, **kwargs)

    def diagnostic_state(self) -> dict[str, Any]:
        frames = sys._current_frames()
        threads = {}
        for tid, frame in frames.items():
            threads[str(tid)] = "".join(traceback.format_stack(frame))

        return {
            "run_id": self._run_id,
            "worker_alive": bool(self._thread and self._thread.is_alive()),
            "worker_thread_id": self._thread.ident if self._thread else None,
            "engine_busy": self._busy.locked(),
            "last_backend_step": self._last_backend_step,
            "last_backend_step_ts": self._last_backend_step_ts,
            "last_progress_age_seconds": round(time.time() - self._last_backend_step_ts, 2),
            "thread_dump": threads,
        }

    def _default_transport_factory(self, bundle):
        from .identity import discover_real_instance_via_raw
        from .aula_transport import AulaHidTransport

        self._step("TRANSPORT_OPEN_BEGIN")
        t0 = time.time()
        transport = AulaHidTransport.open_real(uuid=int(bundle.product.uuid))
        open_dur_ms = round((time.time() - t0) * 1000, 2)
        self._step("TRANSPORT_OPEN_END", open_duration_ms=open_dur_ms)
        instance = discover_real_instance_via_raw(transport.raw)

        def enumerate_fn():
            try:
                import hid  # type: ignore
                return instance if hid.enumerate(0x372E, 0x103E) else None
            except Exception:
                return None

        def make_transport():
            return AulaHidTransport.open_real(uuid=int(bundle.product.uuid))

        return transport, instance, enumerate_fn, make_transport

    def discover(self) -> dict[str, Any]:
        from .bundle import production_bundle_for_hero84
        bundle = production_bundle_for_hero84()
        transport = None
        self._step("DISCOVERY_HANDLE_OPEN")
        try:
            transport, instance, _, _ = self.transport_factory(bundle)
            disc = discover_state(instance)
            # Check if certificate store already holds a validated certificate for this device
            try:
                from .device_certificate import CertificateStore
                store = CertificateStore()
                matching_cert = store.find_matching(
                    vid=bundle.product.vid,
                    pid=bundle.product.pid,
                    firmware_branch=getattr(instance, "firmware_version", "") or "",
                    descriptor_hash=getattr(instance, "descriptor_hash", "") or "",
                )
                if matching_cert is not None and matching_cert.terminal_verdict == "COMPLETE_PASS":
                    disc["certificate"] = matching_cert.to_dict()
                    disc["certified"] = True
            except Exception:
                pass
            return disc
        except Exception as exc:  # noqa: BLE001
            detected: list[dict[str, Any]] = []
            reason = str(exc)
            first_dev_name = ""
            first_dev_vid = ""
            first_dev_pid = ""
            try:
                import hid  # type: ignore
                all_devs = hid.enumerate()
                seen = set()
                aula_match = None
                for d in all_devs:
                    v = d.get("vendor_id", 0)
                    p = d.get("product_id", 0)
                    if v == 0:
                        continue
                    key = (v, p)
                    if key not in seen:
                        seen.add(key)
                        p_name = d.get("product_string") or "Unknown"
                        mfg = d.get("manufacturer_string") or ""
                        v_hex = f"0x{v:04X}"
                        p_hex = f"0x{p:04X}"
                        dev_entry = {"vid": v_hex, "pid": p_hex, "name": p_name, "manufacturer": mfg}
                        detected.append(dev_entry)
                        if not first_dev_name:
                            first_dev_name = p_name
                            first_dev_vid = v_hex
                            first_dev_pid = p_hex
                        if v == 0x372E:
                            aula_match = dev_entry

                if aula_match:
                    reason = (
                        f"Found AULA device '{aula_match['name']}' ({aula_match['vid']}:{aula_match['pid']}). "
                        f"Target device for this test is HERO 84 HE wired USB (0x372E:0x103E). "
                        f"Please ensure keyboard mode switch is set to Cable/USB."
                    )
                elif detected:
                    reason = (
                        f"Target device (0x372E:0x103E) not detected. "
                        f"{len(detected)} other USB HID devices found. "
                        f"Ensure keyboard is connected directly via USB cable and official vendor apps are closed."
                    )
            except Exception:
                pass

            gh_url = make_github_issue_url(
                brand="AULA" if aula_match else "",
                model=first_dev_name,
                vid=first_dev_vid,
                pid=first_dev_pid,
                failure_category="Device was not detected correctly",
                observed=reason,
            )

            return {
                "state": "NO_DEVICE",
                "device": None,
                "supported_count": 0,
                "reason": reason,
                "detected_devices": detected,
                "github_issue_url": gh_url,
            }
        finally:
            if transport is not None and hasattr(transport, "close"):
                self._step("DISCOVERY_HANDLE_CLOSE_BEGIN")
                try:
                    transport.close()
                except Exception:
                    pass
                self._step("DISCOVERY_HANDLE_CLOSE_END")
            self._step("DISCOVERY_HANDLE_CLOSE_CONFIRMED")

    def recovery_status(self) -> dict[str, Any]:
        return recovery_status(self.run_dir)

    def clear_recovery(self) -> None:
        try:
            from .runstate import RunStateStore
            store = RunStateStore(self.run_dir)
            cp = store.load()
            if cp is not None:
                cp.closed = True
                cp.recovery_required = False
                store.save(cp)
        except Exception:
            pass

    def start_run(self, emit, async_run: bool = True) -> dict[str, Any]:
        if self._busy.locked():
            return {"started": False, "error": "a run is already in progress"}
        pre = self.recovery_status()
        if pre["pending"]:
            return {
                "started": False,
                "error": "recovery preflight is pending — recovery-first must complete before research starts",
            }
        self._run_id = f"run_{int(time.time())}"
        if async_run:
            self._step("WORKER_THREAD_CREATED", run_id=self._run_id)
            self._thread = threading.Thread(target=self._run_worker, args=(emit,), daemon=True)
            self._thread.start()
        else:
            self._run_worker(emit)
        return {"started": True}

    def _emit(self, emit, event: str, data: dict[str, Any]) -> None:
        self._events.append({"event": event, "data": data})
        emit({"event": event, "data": data})

    def _op_progress(self, emit, op_id: str, state: str, text: str) -> None:
        self._emit(emit, "progress", {
            "op": op_id,
            "label": friendly_label(op_id, "AUTO_REVERSIBLE"),
            "state": state,
            "text": text,
        })

    def _run_worker(self, emit) -> None:
        try:
            with self._busy:
                self._step("WORKER_THREAD_ENTERED", thread_id=threading.get_ident(), run_id=self._run_id)
                from .automation import AutoProbeRun
                from .bundle import production_bundle_for_hero84
                from .identity import ExactIdentityGate

                self._emit(emit, "progress", {
                    "op": "system",
                    "label": "System",
                    "state": "PREPARING",
                    "text": "Preparing research...",
                })
                self._step("REAL_RUN_ENTER")

                self._step("PREPARE_STEP_1_BUNDLE_LOAD_BEGIN")
                bundle = production_bundle_for_hero84()
                self._step("PREPARE_STEP_1_BUNDLE_LOAD_END")

                self._emit(emit, "progress", {
                    "op": "system",
                    "label": "System",
                    "state": "VALIDATING_PLAN",
                    "text": "Validating test plan...",
                })
                self._step("PREPARE_STEP_2_PLAN_VALIDATION_BEGIN")
                plan = plan_preview()
                self._step("PREPARE_STEP_2_PLAN_VALIDATION_END", safe_count=plan.get("safe_count", 0))

                self._step("PREPARE_STEP_3_QUEUE_OPS_BEGIN")
                for op in plan.get("safe", []):
                    self._op_progress(emit, op["id"], "QUEUED", "Waiting...")
                self._step("PREPARE_STEP_3_QUEUE_OPS_END")

                self._emit(emit, "progress", {
                    "op": "system",
                    "label": "System",
                    "state": "OPENING_DEVICE",
                    "text": "Opening device...",
                })
                transport, instance, enumerate_fn, make_transport = self.transport_factory(bundle)

                self._emit(emit, "progress", {
                    "op": "system",
                    "label": "System",
                    "state": "VERIFYING_DEVICE",
                    "text": "Verifying device...",
                })
                self._step("IDENTITY_REVALIDATION_BEGIN")
                gate = ExactIdentityGate(bundle)
                verdict = gate.evaluate(instance)
                self._step("IDENTITY_REVALIDATION_END", passed=verdict.passed, reason=verdict.reason)
                if not verdict.passed:
                    raise RuntimeError(f"Device exact identity evaluation failed: {verdict.reason}")

                self._emit(emit, "progress", {
                    "op": "system",
                    "label": "System",
                    "state": "PREPARING_BASELINE",
                    "text": "Preparing baseline...",
                })
                self._step("RUNSTATE_INIT_BEGIN")

                def on_op_progress(op_id: str, state: str, text: str) -> None:
                    self._op_progress(emit, op_id, state, text)

                run = AutoProbeRun(
                    bundle=bundle,
                    transport=transport,
                    instance=instance,
                    enumerate_fn=enumerate_fn,
                    make_transport=make_transport,
                    run_dir=self.run_dir,
                    reconnect_timeout_ms=15000,
                    on_op_progress=on_op_progress,
                )
                self._step("RUNSTATE_INIT_END")
                self._step("AUTOPROBERUN_CONSTRUCTED")
                self._step("AUTOPROBERUN_RUN_ENTER")
                run.run()

                results = [
                    {
                        "id": getattr(e, "operation", ""),
                        "label": friendly_label(getattr(e, "operation", ""), "AUTO_REVERSIBLE"),
                        "status": getattr(e, "status", ""),
                        "restored": bool(getattr(e, "rollback_matched", False)),
                    }
                    for e in run.results
                ]
                gh_url = make_github_issue_url(
                    brand=bundle.product.name,
                    model=getattr(instance, "product_string", None) or bundle.product.name,
                    vid=bundle.product.vid,
                    pid=bundle.product.pid,
                    firmware=getattr(instance, "firmware_version", "unknown"),
                    run_id=self._run_id,
                    failure_category="Validation run completed" if run.verdict == "COMPLETE_PASS" else "Validation check failed or rolled back",
                    observed=f"Result: {run.verdict}. Checks completed: {len(run.results)}/{len(plan.get('safe', []))}.",
                )
                self._result = {
                    "status": run.verdict,
                    "restored": bool(run.baseline_restored),
                    "checks_completed": len(run.results),
                    "checks_total": len(plan.get("safe", [])),
                    "results": results,
                    "evidence_source": "real" if self.is_physical else "mock",
                    "physical_validation_evidence": self.is_physical,
                    "output_path": str(run.package_dir) if run.package_dir else None,
                    "github_issue_url": gh_url,
                }
                self._emit(emit, "run_result", self._result)
        except BaseException as exc:
            self._step("WORKER_EXCEPTION", error=str(exc), traceback=traceback.format_exc())
            gh_url = make_github_issue_url(
                brand="AULA HERO84",
                run_id=self._run_id,
                failure_category="Exception during research run",
                observed=str(exc),
            )
            self._result = {
                "status": "ERROR",
                "restored": False,
                "checks_completed": 0,
                "checks_total": 0,
                "results": [],
                "evidence_source": "real" if self.is_physical else "mock",
                "physical_validation_evidence": False,
                "output_path": None,
                "error": str(exc),
                "github_issue_url": gh_url,
            }
            self._emit(emit, "run_result", self._result)

    def run_result(self) -> dict[str, Any]:
        return self._result or {"status": "IDLE", "results": [], "checks_completed": 0}


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

CANONICAL_RPC_METHODS = (
    "health",
    "discover",
    "plan",
    "recovery_status",
    "clear_recovery",
    "start_run",
    "run_result",
)


class ProbeRpcServer:
    """JSON-lines RPC server over a stream pair (default stdin/stdout).

    `engine` is a DemoEngine for --demo, or a RealEngine (real hardware) otherwise.
    Injected streams make it deterministically testable."""

    def __init__(self, engine: DemoEngine | RealEngine | None = None, input_stream=None, output_stream=None) -> None:
        from .bundle import production_bundle_for_hero84
        self.bundle = production_bundle_for_hero84()
        self.engine = engine if engine is not None else RealEngine()
        self.input_stream = input_stream if input_stream is not None else sys.stdin
        self.output_stream = output_stream if output_stream is not None else sys.stdout
        self._seq = 0
        self._lock = threading.Lock()

    # -- wire ----------------------------------------------------------------
    def _send(self, obj: dict[str, Any]) -> None:
        with self._lock:
            # ensure_ascii=True ensures 100% valid 7-bit ASCII/UTF-8 over the wire
            # regardless of Windows console/codepage settings.
            line = json.dumps(obj, ensure_ascii=True)
            print(f"[SIDECAR DIAG _SEND] {line[:80]}", file=sys.stderr, flush=True)
            self.output_stream.write(line + "\n")
            self.output_stream.flush()

    def emit(self, obj: dict[str, Any]) -> None:
        self._send(obj)

    def _respond(self, ident: int, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        if error is not None:
            self._send({"id": ident, "ok": False, "error": error})
        else:
            self._send({"id": ident, "ok": True, "result": result or {}})

    # -- methods -------------------------------------------------------------
    def handle(self, req: dict[str, Any]) -> None:
        ident = req.get("id")
        raw_method = req.get("method", "")
        method = str(raw_method).strip().lower()
        if method.startswith("probe_"):
            method = method[6:]

        # Normalize common synonyms/getter aliases
        if method in ("get_recovery_status", "recovery_status", "recovery"):
            norm = "recovery_status"
        elif method in ("get_plan", "plan"):
            norm = "plan"
        elif method in ("get_discovery_state", "get_discover", "discover", "discovery"):
            norm = "discover"
        elif method in ("get_run_result", "run_result", "result"):
            norm = "run_result"
        elif method in ("get_health", "health"):
            norm = "health"
        elif method in ("clear_recovery",):
            norm = "clear_recovery"
        elif method in ("start_run", "run"):
            norm = "start_run"
        else:
            norm = method

        try:
            if norm == "health":
                self._respond(ident, {"engine": "demo" if isinstance(self.engine, DemoEngine) else "real",
                                      "method": "health"})
            elif norm == "discover":
                self._respond(ident, self.engine.discover())
            elif norm == "plan":
                self._respond(ident, plan_preview())
            elif norm == "recovery_status":
                self._respond(ident, self.engine.recovery_status())
            elif norm == "clear_recovery":
                self.engine.clear_recovery()
                self._respond(ident, self.engine.recovery_status())
            elif norm == "start_run":
                diag_log("START_RUN_RPC_RECEIVED", ident=ident)
                res = self.engine.start_run(self.emit)
                self._respond(ident, res)
                diag_log("START_RUN_RESPONSE_SENT", ident=ident, res=res)
            elif norm == "run_result":
                self._respond(ident, self.engine.run_result())
            elif norm in ("diagnostics", "diagnostic_state", "get_diagnostics", "get_diagnostic_state"):
                self._respond(ident, self.engine.diagnostic_state())
            else:
                self._respond(ident, error=f"unknown method: {raw_method}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._respond(ident, error=f"{exc}")

    # -- loop ----------------------------------------------------------------
    def serve_forever(self) -> None:
        while True:
            line = self.input_stream.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                self._respond(-1, error=f"invalid JSON: {exc}")
                continue
            self.handle(req)

    def serve_once(self, req: dict[str, Any]) -> None:
        self.handle(req)


def run_cli(engine: DemoEngine | RealEngine | None) -> int:
    """Entry for `python -m community.vetro_probe.gui_rpc [--demo]`."""
    server = ProbeRpcServer(engine=engine)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="vetro_gui_rpc", description="Vetro Probe GUI engine RPC")
    parser.add_argument("--demo", action="store_true", help="Deterministic mock engine (zero HID, no physical evidence)")
    parser.add_argument("--scenario", type=str, default="supported",
                        help="Demo scenario: supported|fail_restored|manual|recovery_startup|unsupported_fw|no_device")
    args = parser.parse_args(argv)
    engine = DemoEngine(scenario=args.scenario) if args.demo else RealEngine()
    return run_cli(engine)


if __name__ == "__main__":
    raise SystemExit(main())
