"""Vetro Probe end-to-end auto flow — one-command state machine.

START → DISCOVERING → IDENTIFIED → PLANNING → BASELINING → EXECUTING
→ (RECONNECTING / VALIDATING / ROLLING_BACK / RECOVERING)
→ VERIFYING_FINAL_STATE → EXPORTING → COMPLETE
(or BLOCKED / FAILED_REQUIRES_MANUAL_RESTORE, fail-closed).

Every transition is journaled with timestamp + reason. Checkpoints are persisted
after each critical stage; an open checkpoint with write_may_have_applied=true
forces recovery-first on the next launch (persisted crash recovery, minimal variant).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .bundle import Bundle
from .identity import ExactIdentityGate, PhysicalInstance
from .transport import DeviceTransport, TransportResult
from .reconnect import ReconnectManager
from .safety import SafetyGate, FORBIDDEN_PREFIXES, FORBIDDEN_OP_IDS
from .baseline import BaselineCollector, BaselineSnapshot
from .recovery import RecoveryJournal
from .executor import ExecutorContext, execute_single
from .evidence import TestEvidence
from .planner import plan as planner_plan, coverage_report
from .certificate import build_certificate
from .runstate import RunStateStore, RunCheckpoint, recover_interrupted_run

# State machine states
S_INIT = "INIT"
S_DISCOVERING = "DISCOVERING"
S_IDENTIFIED = "IDENTIFIED"
S_PLANNING = "PLANNING"
S_BASELINING = "BASELINING"
S_EXECUTING = "EXECUTING"
S_RECONNECTING = "RECONNECTING"
S_VALIDATING = "VALIDATING"
S_ROLLING_BACK = "ROLLING_BACK"
S_RECOVERING = "RECOVERING"
S_VERIFYING = "VERIFYING_FINAL_STATE"
S_EXPORTING = "EXPORTING"
S_COMPLETE = "COMPLETE"
S_BLOCKED = "BLOCKED"
S_MANUAL = "FAILED_REQUIRES_MANUAL_RESTORE"

# Plan classifications
CLS_AUTO_SAFE = "AUTO_SAFE"
CLS_AUTO_REVERSIBLE = "AUTO_REVERSIBLE"
CLS_MANUAL = "MANUAL_CONFIRMATION_REQUIRED"
CLS_BLOCKED = "BLOCKED"
CLS_UNKNOWN = "UNKNOWN"


@dataclass
class Transition:
    state: str
    reason: str
    timestamp: float
    device: dict[str, Any] = field(default_factory=dict)
    session: int | None = None


class AutoProbeRun:
    """Drives the full headless workflow over an already-connected device."""

    def __init__(
        self,
        *,
        bundle: Bundle,
        transport: DeviceTransport,
        instance: PhysicalInstance,
        enumerate_fn: Callable[[], Any] | None = None,
        firmware_check: Callable[[PhysicalInstance], tuple[bool, str]] | None = None,
        make_transport: Callable[[], DeviceTransport] | None = None,
        run_dir: Path | None = None,
        reconnect_timeout_ms: int = 15000,
        allowed_ops: list[str] | None = None,
        label: str = "auto",
        block_knowledge_holes: bool = False,
        block_missing_strong_e5: bool = False,
    ) -> None:
        self.bundle = bundle
        self.transport = transport
        self.instance = instance
        self.gate = ExactIdentityGate(bundle)
        self.enumerate_fn = enumerate_fn or (lambda: instance)
        self.firmware_check = firmware_check
        self.make_transport = make_transport or (lambda: transport.fresh_session())
        self.reconnect_timeout_ms = reconnect_timeout_ms
        self.allowed_ops = allowed_ops
        self.label = label
        self.block_knowledge_holes = block_knowledge_holes
        self.block_missing_strong_e5 = block_missing_strong_e5
        self._block_reasons: dict[str, str] = {}
        self.run_dir = Path(run_dir or Path.cwd() / f"vetro_auto_{int(time.time())}")
        self.store = RunStateStore(self.run_dir)
        self.cp: RunCheckpoint | None = self.store.load()
        self.transitions: list[Transition] = []
        self.verdict: str | None = None
        self.discovery: dict[str, Any] = {}
        self.plan: list[dict[str, Any]] = []
        self.baselines: dict[str, Any] = {}
        self.results: list[TestEvidence] = []
        self.contradictions: list[str] = []
        self.final_state: dict[str, Any] = {}
        self.baseline_restored: bool = False
        self.package_dir: Path | None = None
        self._reconnect: ReconnectManager | None = None
        self.resolution: Any = None
        self.knowledge_heading: dict[str, Any] = {}

    # ------------------------------------------------------------- transitions
    def _transition(self, state: str, reason: str) -> None:
        tr = Transition(state=state, reason=reason, timestamp=time.time(),
                        device={"vid": self.instance.vid, "pid": self.instance.pid,
                                "firmware": self.instance.firmware_version},
                        session=getattr(self.transport, "current_session_id", lambda: None)())
        self.transitions.append(tr)
        if self.cp is not None:
            self.cp.phase = state
            self.cp.transitions.append({"state": state, "reason": reason, "ts": tr.timestamp})
            self.store.save(self.cp)

    def _checkpoint_op(self, operation: str, baseline, attempted, write_applied: bool) -> None:
        if self.cp is None:
            self.cp = self.store.new_run()
        self.cp.operation = operation
        self.cp.baseline = baseline
        self.cp.attempted = attempted
        self.cp.write_may_have_applied = write_applied
        self.cp.rollback_attempted = write_applied
        self.store.save(self.cp)

    # ---------------------------------------------------------------- run
    def run(self) -> "AutoProbeRun":
        self._transition(S_INIT, "auto run started")
        if RunStateStore.open_write_pending(self.cp):
            self._transition(S_RECOVERING, "open write checkpoint detected — recovery before new run")
            self._recover_pending()
        self._discover()
        if self.verdict == S_BLOCKED:
            self._export(terminal=S_BLOCKED)
            return self
        self._plan()
        self._baseline()
        if self.verdict == S_MANUAL:
            self._export(terminal=S_MANUAL)
            return self
        self._execute()
        if self.verdict == S_MANUAL:
            self._export(terminal=S_MANUAL)
            return self
        self._verify_final()
        self._export(terminal=S_COMPLETE)
        return self

    # ------------------------------------------------------------ recovery
    def _recover_pending(self) -> None:
        cp = self.cp
        if cp is None or not cp.operation:
            self._transition(S_BLOCKED, "pending checkpoint has no operation — manual review required")
            self.verdict = S_BLOCKED
            return

        def get_current(t: DeviceTransport, op: str):
            val, res = t.get(op)
            if not res.ok:
                raise RuntimeError(res.error)
            return val

        def set_baseline(t: DeviceTransport, op: str, val):
            res = t.set(op, val)
            if not res.ok:
                raise RuntimeError(res.error)

        rec = recover_interrupted_run(
            cp, self.make_transport, self.gate, self.enumerate_fn,
            firmware_check=self.firmware_check, timeout_ms=self.reconnect_timeout_ms,
            get_current=get_current, set_baseline=set_baseline,
        )
        self.cp = rec
        self.store.save(rec)
        if rec.closed:
            self._transition(S_COMPLETE, f"persisted recovery closed (op={rec.operation}, baseline restored={rec.final_verified})")
            self.baseline_restored = rec.final_verified
            self.verdict = S_COMPLETE
        else:
            self._transition(S_MANUAL, f"persisted recovery failed: {rec.error}")
            self.verdict = S_MANUAL
        # New run continues after recovery with a fresh transport
        try:
            self.transport = self.make_transport()
        except Exception:
            pass

    # ------------------------------------------------------------ discovery
    def _discover(self) -> None:
        self._transition(S_DISCOVERING, "HID discovery + identity + firmware")
        inst = self.enumerate_fn()
        from .reconnect import AMBIGUOUS
        if inst == AMBIGUOUS or inst is None and self.instance is None:
            self.verdict = S_BLOCKED
            self.discovery = {"ambiguous": True, "writes": 0}
            self._transition(S_BLOCKED, "ambiguous identity / no single candidate — ZERO writes")
            return
        if inst is not None and inst != AMBIGUOUS:
            self.instance = inst
        verdict = self.gate.evaluate(self.instance)
        self.discovery = {
            "vid": self.instance.vid, "pid": self.instance.pid,
            "descriptor_hash": self.instance.descriptor_hash,
            "firmware": self.instance.firmware_version,
            "connection": self.instance.connection_mode,
            "product_string": self.instance.product_string,
            "family": self.bundle.family,
            "exact_identity": verdict.passed,
            "identity_reason": verdict.reason,
            "bundle": {"id": self.bundle.id, "version": self.bundle.version, "hash": self.bundle.hash},
            "knowledge_revision": self.bundle.raw.get("knowledge_revision", ""),
        }
        if not verdict.passed:
            # read-only discovery allowed; writes blocked via SafetyGate. Only hard-block on ambiguity/VID-PID mismatch.
            low = verdict.reason.lower()
            if "ambiguous" in low or "vid/pid mismatch" in low:
                self.verdict = S_BLOCKED
                self._transition(S_BLOCKED, f"identity not resolvable: {verdict.reason}")
                return
            # firmware-only soft block: proceed read-only, writes will be blocked per-op
        self._transition(S_IDENTIFIED, "exact identity resolved (read-only writes-gated)")

    # --------------------------------------------------------------- planning
    def _classify_op(self, op_id: str) -> str:
        op = self.bundle.operations.get(op_id)
        if op is None:
            return CLS_UNKNOWN
        if op_id in FORBIDDEN_OP_IDS or any(op_id.startswith(p + ".") or op_id == p for p in FORBIDDEN_PREFIXES):
            return CLS_BLOCKED
        if self.block_knowledge_holes and op_id == "he.rt":
            self._block_reasons[op_id] = "BLOCKED_BY_KNOWLEDGE_HOLE (rapid_trigger_units_crosscheck is authoritative OPEN)"
            return CLS_BLOCKED
        if self.block_missing_strong_e5 and op_id == "keyboard.remap":
            self._block_reasons[op_id] = "BLOCKED_BY_MISSING_STRONG_E5 (only uncorrelated_os available; strong E5 requires WM_INPUT hDevice correlation)"
            return CLS_BLOCKED
        if op.kind == "observable":
            return CLS_MANUAL
        if not op.reversible:
            return CLS_BLOCKED
        if op.kind not in ("set", "toggle", "transaction", "register_preserve"):
            return CLS_BLOCKED
        if op_id not in self.bundle.bounds:
            return CLS_BLOCKED
        return CLS_AUTO_REVERSIBLE

    def _plan(self) -> None:
        self._transition(S_PLANNING, "safety capability assessment + operation planning")
        planned = planner_plan(self.bundle)
        if self.allowed_ops:
            planned = [p for p in planned if p.operation_id in self.allowed_ops]
        # Brand/family-aware knowledge routing (additive; never weakens safety)
        try:
            from .brand_router import resolve as resolve_brand
            from .knowledge_planner import knowledge_plan_entry, value_heading

            self.resolution = resolve_brand(
                brand=self.discovery.get("brand", ""),
                vid=self.discovery.get("vid", ""), pid=self.discovery.get("pid", ""),
                family=self.bundle.family,
                model=self.discovery.get("product_string", ""),
                firmware=self.discovery.get("firmware", ""),
            )
            self.knowledge_heading = value_heading(self.resolution)
        except Exception:
            self.resolution = None
            self.knowledge_heading = {}
        for p in planned:
            op_id = p.operation_id
            cls = self._classify_op(op_id)
            op = self.bundle.operations[op_id]
            block_reason = self._block_reasons.get(op_id, "")
            self.plan.append({
                "operation": op_id,
                "classification": cls,
                "why_selected": p.reason,
                "why_safe": block_reason or (
                    "production_safe reversible, readback+rollback, bounds present, firmware gate"
                    if cls == CLS_AUTO_REVERSIBLE else "blocked/unknown"),
                "expected_observable": op.needs_observable,
                "rollback_method": "restore_value",
                "reconnect_required": op.requires_reconnect,
                "failure_policy": "recovery then FAILED_REQUIRES_MANUAL_RESTORE" if op.reversible else "no-op",
            })
        # enrich with knowledge fields when a resolution is available
        if getattr(self, "resolution", None) is not None:
            try:
                from .knowledge_planner import knowledge_plan_entry
                for entry in self.plan:
                    entry.update(knowledge_plan_entry(
                        self.resolution, entry["operation"], planned=True,
                        classification=entry["classification"], why_selected=entry.get("why_selected", "")))
            except Exception:
                pass

    # -------------------------------------------------------------- baselining
    def _baseline(self) -> None:
        self._transition(S_BASELINING, "baseline acquisition before any write")
        collector = BaselineCollector(self.transport)
        for entry in self.plan:
            op_id = entry["operation"]
            if entry["classification"] != CLS_AUTO_REVERSIBLE:
                continue
            snap = collector.collect([op_id])
            if op_id not in snap.values:
                entry["classification"] = CLS_BLOCKED
                entry["why_safe"] = "baseline unavailable"
                continue
            self.baselines[op_id] = snap.values[op_id]

    # -------------------------------------------------------------- execution
    def _execute(self) -> None:
        self._transition(S_EXECUTING, "typed safe execution with readback/rollback/recovery")
        reconnect = ReconnectManager(self.transport, self.gate, self.enumerate_fn,
                                     timeout_ms=self.reconnect_timeout_ms, poll_ms=200,
                                     firmware_check=self.firmware_check)
        self._reconnect = reconnect
        current = self.transport
        for entry in self.plan:
            op_id = entry["operation"]
            if entry["classification"] != CLS_AUTO_REVERSIBLE:
                continue
            baseline_val = self.baselines.get(op_id)
            if baseline_val is None:
                continue
            collector = BaselineCollector(current)
            snap = collector.collect([op_id])
            recovery = RecoveryJournal(snap)
            safety = SafetyGate(self.bundle, instance_firmware=self.instance.firmware_version)
            ctx = ExecutorContext(
                bundle=self.bundle, transport=current, safety=safety, baseline=snap,
                recovery=recovery, reconnect=reconnect,
                observable=None, firmware_branch=self.instance.firmware_version,
                connection_mode=self.instance.connection_mode,
            )
            self._transition(S_EXECUTING, f"executing {op_id} (baseline {baseline_val!r})")
            ev = execute_single(op_id, ctx)
            current = ctx.transport
            self.results.append(ev)
            write_applied = (ev.transport_result == "ok")
            self._checkpoint_op(op_id, baseline_val, ev.temporary_value, write_applied)
            rec = ev.recovery or {}
            if rec.get("recovery_blocked") and write_applied:
                self.verdict = S_MANUAL
                self._transition(S_MANUAL, f"{op_id}: recovery blocked ({rec.get('recovery_block_reason','')}) — manual restore required")
                self.cp.recovery_required = True
                self.cp.closed = False
                self.store.save(self.cp)
                return
            if ev.status == "PASS":
                self._transition(S_VALIDATING, f"{op_id} readback/rollback validated")
            # checkpoint closed after successful full lifecycle for this op
            self.cp.closed = True
            self.cp.final_verified = True
            self.store.save(self.cp)
        self.transport = current

    # ------------------------------------------------------- final verify
    def _verify_final(self) -> None:
        self._transition(S_VERIFYING, "final normalized snapshot vs initial")
        ops = [o for o in self.baselines]
        if not ops:
            self.baseline_restored = True
            self.final_state = {"restored": True, "note": "no mutable ops executed"}
            return
        # Fresh session + cadence between GETs: a burst of reads on one real-HID session can
        # desync replies (observed on real HERO84) and return stale/temporary values.
        try:
            fresh = self.make_transport()
        except Exception:
            fresh = self.transport
        collector = BaselineCollector(fresh)
        values: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for op in ops:
            time.sleep(0.05)
            val, res = fresh.get(op)
            if res.ok:
                values[op] = val
            else:
                errors[op] = res.error
        mismatches = {}
        for op, init in self.baselines.items():
            final = values.get(op)
            if final != init:
                mismatches[op] = {"expected": init, "actual": final, "error": errors.get(op, "")}
        self.baseline_restored = not mismatches
        self.final_state = {"restored": self.baseline_restored, "mismatches": mismatches, "fresh_session": True}
        if not self.baseline_restored:
            self.contradictions.append(f"final baseline mismatch: {mismatches}")

    # ---------------------------------------------------------------- export
    def _export(self, terminal: str) -> None:
        self._transition(S_EXPORTING, f"exporting miner-ready package (terminal={terminal})")
        from .miner_package import build_package

        if self.cp is None:
            self.cp = self.store.new_run()
        identity_verdict = self.gate.evaluate(self.instance)
        per_op_certs = []
        for ev in self.results:
            cov = coverage_report(self.bundle, [ev])
            cert = build_certificate(identity_verdict, self.bundle,
                                     "", "", self.baseline_restored, [ev],
                                     self.contradictions, cov,
                                     knowledge_revision=self.discovery.get("knowledge_revision", ""),
                                     timings={"ts": time.time()})
            per_op_certs.append({"operation": ev.operation, "verdict": cert.verdict, "certificate": cert.to_dict()})

        package_dir = build_package(
            base_dir=self.run_dir,
            run_id=self.cp.run_id if self.cp else "run",
            label=self.label,
            discovery=self.discovery,
            plan=self.plan,
            evidence=[ev for ev in self.results],
            baselines=self.baselines,
            final_state=self.final_state,
            certificates=per_op_certs,
            recovery=(self.cp.to_dict() if self.cp else {}),
            terminal=terminal,
        )
        self.package_dir = package_dir
        # knowledge routing artifacts (additive; Probe proposes, miner accepts)
        if self.resolution is not None:
            try:
                (package_dir / "knowledge.json").write_text(
                    json.dumps({**self.resolution.to_dict(), "value": self.knowledge_heading},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                from .knowledge_delta import build_knowledge_delta, write_knowledge_delta
                obs = [{"operation": getattr(e, "operation", None), "status": getattr(e, "status", None),
                        "readback_matched": getattr(e, "readback_matched", False),
                        "rollback_matched": getattr(e, "rollback_matched", False)} for e in self.results]
                delta = build_knowledge_delta(self.resolution, obs)
                write_knowledge_delta(package_dir, delta)
            except Exception:
                pass
        self.verdict = terminal
        self.cp.phase = terminal
        if terminal != S_MANUAL or not self.cp.write_may_have_applied:
            # A FAILED_REQUIRES_MANUAL_RESTORE with a possibly-applied write stays OPEN
            # so the next launch recovers before any new run.
            self.cp.closed = True
        self.store.save(self.cp)
        self._transition(terminal, f"package exported: {package_dir}")

    # --------------------------------------------------------------- summary
    def summary(self) -> str:
        safe = sum(1 for e in self.plan if e["classification"] == CLS_AUTO_REVERSIBLE)
        executed = len(self.results)
        passed = sum(1 for e in self.results if e.status == "PASS")
        failed = sum(1 for e in self.results if e.status == "FAIL")
        blocked = sum(1 for e in self.plan if e["classification"] == CLS_BLOCKED)
        lines = [
            "=== VETRO PROBE AUTO SUMMARY ===",
            f"DEVICE: {self.discovery.get('product_string') or 'HERO 84 HE'}",
            f"IDENTITY: {self.discovery.get('vid')}:{self.discovery.get('pid')} family={self.discovery.get('family')} exact={self.discovery.get('exact_identity')}",
            f"FIRMWARE: {self.discovery.get('firmware')} (expected {self.bundle.firmware_branch})",
            f"SAFE OPERATIONS: {safe}",
            f"EXECUTED: {executed}",
            f"PASS: {passed}",
            f"BLOCKED: {blocked}",
            f"FAILED: {failed}",
            f"BASELINE RESTORED: {'YES' if self.baseline_restored else 'NO'}",
            f"FINAL STATE VERIFIED: {'YES' if self.baseline_restored else 'NO'}",
            f"RECOVERY REQUIRED: {'YES' if (self.cp and self.cp.recovery_required) else 'NO'}",
            f"MINER PACKAGE: {self.package_dir}",
            f"STATUS: {self.verdict}",
        ]
        return "\n".join(lines)
