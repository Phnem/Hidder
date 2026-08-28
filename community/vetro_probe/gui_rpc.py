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

# Friendly labels for the six physically-validated ops (exact HERO84/FW0216).
OP_LABELS = {
    "keyboard.profile": "Profiles",
    "keyboard.polling": "Polling rate",
    "device.win_lock": "Windows Lock",
    "he.deadzone": "Deadzone",
    "he.actuation": "Actuation",
    "light.brightness": "Brightness",
}

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


def label_for(op_id: str) -> str:
    return OP_LABELS.get(op_id, op_id)


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

    def __init__(self, engine=None, input_stream=None, output_stream=None) -> None:
        from .bundle import production_bundle_for_hero84
        self.bundle = production_bundle_for_hero84()
        self.engine = engine
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

        params = req.get("params", {}) or {}
        try:
            if norm == "health":
                self._respond(ident, {"engine": "real" if self.engine is None else "demo",
                                      "method": "health"})
            elif norm == "discover":
                self._respond(ident, self._discover())
            elif norm == "plan":
                self._respond(ident, plan_preview())
            elif norm == "recovery_status":
                self._respond(ident, self._recovery_status())
            elif norm == "clear_recovery":
                if isinstance(self.engine, DemoEngine):
                    self.engine.clear_recovery()
                self._respond(ident, self._recovery_status())
            elif norm == "start_run":
                self._respond(ident, self._start_run())
            elif norm == "run_result":
                self._respond(ident, self._run_result())
            else:
                self._respond(ident, error=f"unknown method: {raw_method}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._respond(ident, error=f"{exc}")

    def _discover(self) -> dict[str, Any]:
        if isinstance(self.engine, DemoEngine):
            return self.engine.discover()
        return self._real_discover()

    def _real_discover(self) -> dict[str, Any]:
        try:
            from .identity import discover_real_instance_via_raw
            from .aula_transport import AulaHidTransport
            transport = AulaHidTransport.open_real(uuid=int(self.bundle.product.uuid))
            instance = discover_real_instance_via_raw(transport.raw)
            return discover_state(instance)
        except Exception as exc:  # noqa: BLE001
            return {"state": "NO_DEVICE", "device": None, "supported_count": 0,
                    "reason": str(exc)}

    def _recovery_status(self) -> dict[str, Any]:
        run_dir = Path("vetro_gui_run")
        if isinstance(self.engine, DemoEngine):
            return self.engine.recovery_status()
        return recovery_status(run_dir)

    def _start_run(self) -> dict[str, Any]:
        if isinstance(self.engine, DemoEngine):
            return self.engine.start_run(self.emit)
        return self._real_start_run()

    def _real_start_run(self) -> dict[str, Any]:
        # Real hardware path: delegates to the EXISTING AutoProbeRun (thin, gated).
        # Not exercised by automated tests; requires an explicit GUI hardware smoke.
        run_dir = Path("vetro_gui_run")
        pre = recovery_status(run_dir)
        if pre["pending"]:
            return {"started": False,
                    "error": "recovery preflight is pending — recovery-first must complete before research starts"}
        thread = threading.Thread(target=self._real_run_worker, args=(run_dir,), daemon=True)
        thread.start()
        return {"started": True}

    def _real_run_worker(self, run_dir: Path) -> None:
        try:
            from .automation import AutoProbeRun
            from .identity import discover_real_instance_via_raw
            from .aula_transport import AulaHidTransport
            from .bundle import production_bundle_for_hero84

            bundle = production_bundle_for_hero84()
            plan = plan_preview()
            for op in plan.get("safe", []):
                self.emit({"event": "progress", "data": {"op": op["id"], "label": op["label"], "state": "QUEUED", "text": "Waiting..."}})

            transport = AulaHidTransport.open_real(uuid=int(bundle.product.uuid))
            instance = discover_real_instance_via_raw(transport.raw)

            def enumerate_fn():
                try:
                    import hid  # type: ignore
                    return instance if hid.enumerate(0x372E, 0x103E) else None
                except Exception:
                    return None

            def on_op_progress(op_id: str, state: str, text: str) -> None:
                self.emit({"event": "progress", "data": {
                    "op": op_id,
                    "label": friendly_label(op_id, "AUTO_REVERSIBLE"),
                    "state": state,
                    "text": text,
                }})

            run = AutoProbeRun(bundle=bundle, transport=transport, instance=instance,
                               enumerate_fn=enumerate_fn,
                               make_transport=lambda: AulaHidTransport.open_real(uuid=int(bundle.product.uuid)),
                               run_dir=run_dir, reconnect_timeout_ms=15000,
                               on_op_progress=on_op_progress)
            run.run()
            result = {
                "status": run.verdict,
                "restored": run.baseline_restored,
                "checks_completed": len(run.results),
                "checks_total": len(run.results),
                "results": [{"id": getattr(e, "operation", ""), "status": getattr(e, "status", ""),
                             "restored": bool(getattr(e, "rollback_matched", False))} for e in run.results],
                "evidence_source": "real",
                "physical_validation_evidence": True,
                "output_path": str(run.package_dir) if run.package_dir else None,
            }
        except Exception as exc:  # noqa: BLE001
            result = {"status": "ERROR", "restored": False, "checks_completed": 0, "checks_total": 0,
                      "results": [], "evidence_source": "real",
                      "physical_validation_evidence": False, "output_path": None,
                      "error": str(exc)}
        self.emit({"event": "run_result", "data": result})

    def _run_result(self) -> dict[str, Any]:
        if isinstance(self.engine, DemoEngine):
            return self.engine._result or {"status": "IDLE", "results": [], "checks_completed": 0}
        return {"status": "UNKNOWN", "note": "real run result is delivered as a run_result event"}

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


def run_cli(engine: DemoEngine | None) -> int:
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
    engine = DemoEngine(scenario=args.scenario) if args.demo else None
    return run_cli(engine)


if __name__ == "__main__":
    raise SystemExit(main())
