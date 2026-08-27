"""Headless CLI vertical slice for Vetro Probe.

Flow per spec:
Preview bundle -> ExactIdentityGate -> PassiveSafeVerification -> BaselineSnapshot
-> RecoveryJournal armed -> one reversible operation -> readback -> rollback -> Final Restore Gate -> vetrojson
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure DB on path for aula_kb_v3 / pdevemu imports when running via `python -m community.vetro_probe.cli`
_DB = Path(__file__).resolve().parents[2] / "DB"
if str(_DB) not in sys.path:
    sys.path.insert(0, str(_DB))

from .bundle import load_bundle, example_hero84_bundle
from .safety import SafetyGate
from .identity import ExactIdentityGate, mock_hero84_instance, discover_real_instance_via_raw
from .transport import FakeTransport, DeviceTransport
from .baseline import BaselineCollector
from .recovery import RecoveryJournal
from .executor import ExecutorContext, execute_single
from .planner import plan, coverage_report
from .certificate import build_certificate
from .reconnect import ReconnectManager
from .observable import FakeObservableListener, RealCompositeListener


def _load_bundle_with_fallback(bundle_path: Path | None, operation_id: str):
    # Try explicit path, then production registry bundle, then synthetic example
    if bundle_path and Path(bundle_path).is_file():
        b = load_bundle(Path(bundle_path))
        return b
    # Try production bundle from registry (no hardcoded opcode truth)
    try:
        from .bundle import production_bundle_for_hero84

        b = production_bundle_for_hero84()
        if operation_id not in b.operations:
            # fallback to first available
            operation_id = list(b.operations.keys())[0]
        return b
    except Exception:
        b = example_hero84_bundle()
        if operation_id not in b.operations:
            operation_id = list(b.operations.keys())[0]
        return b


def _create_fake_transport(bundle, operation_id: str):
    initial_state = {
        "he.actuation": 0.5,
        "he.rt": 1,
        "keyboard.polling": 3,
        "light.rgb_core": 0xFF0000,
        "keyboard.profile": 0,
        "device.win_lock": 0,
    }
    if operation_id not in initial_state:
        bounds = bundle.bounds.get(operation_id, {})
        initial_state[operation_id] = bounds.get("safe_values", [0])[0] if bounds.get("safe_values") else 0
    transport: DeviceTransport = FakeTransport(initial_state=initial_state, reconnect_ops={"keyboard.polling"} if bundle.operations[operation_id].requires_reconnect else set())
    return transport


def _create_sim_transport(bundle):
    # Use pdevemu simulator for integration without physical hardware
    try:
        from pdevemu.aula_kb_v3_sim import AulaKbV3SimDevice
    except ImportError:
        from DB.pdevemu.aula_kb_v3_sim import AulaKbV3SimDevice  # type: ignore
    try:
        import aula_kb_v3.registry as reg  # type: ignore
    except ImportError:
        import DB.aula_kb_v3.registry as reg  # type: ignore
    hero = reg.resolve_by_uuid(18691697672197)
    sim = AulaKbV3SimDevice(product=hero)
    # Ensure simulator has plausible baseline for HE actuation
    # Simulator defaults actuation 1.0 for all positions, profile 0 etc.
    from .aula_transport import AulaHidTransport

    return AulaHidTransport.from_sim(sim)


def _create_real_transport(bundle) -> tuple[DeviceTransport, Any]:
    """Open real hardware and resolve PhysicalInstance. Returns (transport, instance)."""
    from .aula_transport import AulaHidTransport
    from .identity import discover_real_instance_via_raw

    # Determine uuid from bundle product
    uuid = None
    try:
        uuid = int(bundle.product.uuid) if bundle.product.uuid else 18691697672197
    except Exception:
        uuid = 18691697672197
    transport = AulaHidTransport.open_real(uuid=uuid)
    # Build PhysicalInstance from raw
    # raw is inside transport.raw (HidRawTransport)
    raw = getattr(transport, "raw", None)
    instance = discover_real_instance_via_raw(raw, getattr(raw, "path", None))
    # Overwrite descriptor_hash with something stable from product uuid if needed
    # Keep vid/pid from bundle to satisfy ExactIdentityGate (bundle expects 0x372E:0x103E)
    return transport, instance


def run_batch(
    bundle_path: Path | None = None,
    output_path: Path | None = None,
    use_real: bool = False,
    use_sim: bool = False,
    max_ops: int | None = None,
) -> Path:
    """Multi-op batch: planner -> execute all planned reversible ops sequentially."""
    import time

    bundle = _load_bundle_with_fallback(bundle_path, "he.actuation")
    planned = plan(bundle)
    if max_ops is not None:
        planned = planned[:max_ops]
    if not planned:
        raise RuntimeError("planner produced no operations")

    # Physical instance + transport
    if use_real:
        transport, instance = _create_real_transport(bundle)
    elif use_sim:
        transport = _create_sim_transport(bundle)
        instance = mock_hero84_instance()
        try:
            instance = discover_real_instance_via_raw(getattr(transport, "raw", None))
        except Exception:
            pass
    else:
        instance = mock_hero84_instance()
        # For fake batch, create FakeTransport with all planned ops in initial state
        fake_state = {p.operation_id: 0.5 for p in planned}
        # Fill realistic defaults per op
        for p in planned:
            b = bundle.bounds.get(p.operation_id, {})
            sv = b.get("safe_values", [0])[0] if b.get("safe_values") else 0
            fake_state[p.operation_id] = sv
            # baseline should be different from safe delta, so set baseline to something else
            if p.operation_id == "he.actuation":
                fake_state[p.operation_id] = 1.0
            if p.operation_id == "keyboard.polling":
                fake_state[p.operation_id] = 3
        transport = FakeTransport(initial_state=fake_state, reconnect_ops={"keyboard.polling"} if any(p.operation_id == "keyboard.polling" for p in planned) else set())

    gate = ExactIdentityGate(bundle)
    verdict = gate.evaluate(instance)
    if not verdict.passed:
        raise RuntimeError(f"ExactIdentityGate BLOCKED: {verdict.reason}")

    # Shared baseline for all ops
    total_start = time.time()
    t_passive = time.time()
    collector = BaselineCollector(transport)
    ops_for_baseline = [p.operation_id for p in planned]
    ops_for_baseline = list(dict.fromkeys(ops_for_baseline))
    snap = collector.collect(ops_for_baseline)
    t_baseline_ms = int((time.time() - t_passive) * 1000)
    # Check at least one baseline
    if not snap.values:
        raise RuntimeError(f"Baseline unavailable for batch: {snap.errors}")

    passive_ok, _ = collector.passive_safe_verification(ops_for_baseline)
    if not passive_ok:
        raise RuntimeError("READ_PATH FAIL")

    recovery = RecoveryJournal(snap)
    safety = SafetyGate(bundle, instance_firmware=instance.firmware_version)

    # Observable: real hardware uses Win32 listeners, fake/sim use Fake
    observable = RealCompositeListener() if use_real else FakeObservableListener(auto_pass=True)

    # Need per-op reconnect handling: create a ReconnectManager that can be reused
    # For sim/real, we need to handle polling specially; for fake, auto-simulate
    def enumerate_fn():
        if use_real:
            try:
                import hid  # type: ignore

                devs = hid.enumerate(0x372E, 0x103E)
                return instance if devs else None
            except Exception:
                return None
        return instance

    # We'll create a single ReconnectManager for batch but reuse per op that needs it
    batch_reconnect = ReconnectManager(transport, gate, enumerate_fn, timeout_ms=7000, poll_ms=200)

    evidences = []
    for planned_op in planned:
        op_id = planned_op.operation_id
        # Skip if baseline missing for this op and it's reversible
        if op_id not in snap.values and bundle.operations[op_id].reversible:
            from .evidence import TestEvidence

            ev_skip = TestEvidence(
                operation=op_id,
                safe_command_id=op_id,
                firmware_branch=instance.firmware_version,
                connection_mode=instance.connection_mode,
                baseline_value=None,
                temporary_value=None,
                status="BLOCKED",
                error="baseline unavailable",
                bundle_hash=bundle.hash,
            )
            ev_skip.evidence_strength = []
            evidences.append(ev_skip)
            continue
        # Only give reconnect manager to ops that require it
        op_reconnect = batch_reconnect if bundle.operations[op_id].requires_reconnect else None
        ctx = ExecutorContext(
            bundle=bundle,
            transport=transport,
            safety=safety,
            baseline=snap,
            recovery=recovery,
            reconnect=op_reconnect,
            observable=observable,
            firmware_branch=instance.firmware_version,
            connection_mode=instance.connection_mode,
        )
        ev = execute_single(op_id, ctx)
        evidences.append(ev)
        # Executor may have advanced to a fresh session (reconnect ops); track latest.
        transport = ctx.transport

    t_exec_ms = int((time.time() - t_passive) * 1000)  # approximate
    t_final = time.time()
    final_collector = BaselineCollector(transport)
    final_snap = final_collector.collect(ops_for_baseline)
    baseline_restored = recovery.final_matches_initial(final_snap)
    contradictions: list[str] = []
    if not baseline_restored:
        diff = recovery.final_diff(final_snap)
        contradictions.append(f"final baseline mismatch: {diff}")
        rec = recovery.recover_all(transport)
        final_snap2 = final_collector.collect(ops_for_baseline)
        baseline_restored = recovery.final_matches_initial(final_snap2)
        if not baseline_restored:
            contradictions.append(f"recovery failed: {rec}")
        final_snap = final_snap2
    t_final_ms = int((time.time() - t_final) * 1000)
    total_ms = int((time.time() - total_start) * 1000)
    coverage = coverage_report(bundle, evidences)
    timings = {"baseline_ms": t_baseline_ms, "execute_ms": t_exec_ms, "final_ms": t_final_ms, "total_ms": total_ms}
    knowledge_rev = bundle.raw.get("knowledge_revision", "") if isinstance(bundle.raw, dict) else ""
    cert = build_certificate(verdict, bundle, snap.hash, final_snap.hash, baseline_restored, evidences, contradictions, coverage, knowledge_revision=knowledge_rev, timings=timings)
    out = output_path or Path.cwd() / f"{bundle.product.name.replace(' ', '_')}-batch-{instance.firmware_version}.vetrojson"
    cert.write(out)
    return out


def run_headless(
    bundle_path: Path | None = None,
    output_path: Path | None = None,
    operation_id: str = "he.actuation",
    demo: bool = False,
    use_real: bool = False,
    use_sim: bool = False,
) -> Path:
    import time

    bundle = _load_bundle_with_fallback(bundle_path, operation_id)
    # If operation_id still not in bundle after fallback, pick first
    if operation_id not in bundle.operations:
        operation_id = list(bundle.operations.keys())[0]

    # 2. Physical instance + transport selection
    if use_real:
        transport, instance = _create_real_transport(bundle)
    elif use_sim:
        transport = _create_sim_transport(bundle)
        # For sim, instance is mock but with sim product
        instance = mock_hero84_instance()
        # Use simulator's product uuid to build instance descriptor
        try:
            # override with sim product info
            instance = discover_real_instance_via_raw(getattr(transport, "raw", None))
        except Exception:
            pass
    else:
        instance = mock_hero84_instance()
        transport = _create_fake_transport(bundle, operation_id)

    gate = ExactIdentityGate(bundle)
    verdict = gate.evaluate(instance)
    if not verdict.passed:
        # For real hardware, try to close transport
        try:
            if hasattr(transport, "raw") and hasattr(transport.raw, "close"):
                transport.raw.close()
        except Exception:
            pass
        raise RuntimeError(f"ExactIdentityGate BLOCKED: {verdict.reason}")

    # For reconnect ops we need enumerate_fn
    def enumerate_fn():
        if use_real:
            # Real re-enumeration: try to discover again via HidRawTransport
            try:
                from .hid_raw import HidRawTransport

                # Quick probe: try to open again; if fails return None
                # We don't create full transport here, just return instance if device present
                # Use discovery via hid enumeration
                import hid  # type: ignore

                devs = hid.enumerate(0x372E, 0x103E)
                if devs:
                    return instance
                return None
            except Exception:
                return None
        return instance

    reconnect = None
    if bundle.operations[operation_id].requires_reconnect:
        reconnect = ReconnectManager(transport, gate, enumerate_fn, timeout_ms=7000, poll_ms=200)

    total_start = time.time()

    # 4. Passive safe verification (GET before write)
    t_passive = time.time()
    collector = BaselineCollector(transport)
    # collect baseline for all planned ops + target op
    planned = plan(bundle)
    ops_for_baseline = [p.operation_id for p in planned] + [operation_id]
    ops_for_baseline = list(dict.fromkeys(ops_for_baseline))
    snap = collector.collect(ops_for_baseline)
    t_baseline_ms = int((time.time() - t_passive) * 1000)
    if operation_id not in snap.values:
        raise RuntimeError(f"Baseline unavailable for {operation_id}: {snap.errors.get(operation_id)} — BLOCKED")

    passive_ok, _ = collector.passive_safe_verification(ops_for_baseline)
    if not passive_ok:
        raise RuntimeError("READ_PATH FAIL — no passive safe reads")

    # 5. RecoveryJournal armed
    recovery = RecoveryJournal(snap)

    # 6. SafetyGate (firmware strict for writes)
    safety = SafetyGate(bundle, instance_firmware=instance.firmware_version)

    observable = RealCompositeListener() if use_real else FakeObservableListener(auto_pass=True)

    # 7. Executor context
    ctx = ExecutorContext(
        bundle=bundle,
        transport=transport,
        safety=safety,
        baseline=snap,
        recovery=recovery,
        reconnect=reconnect,
        observable=observable,
        firmware_branch=instance.firmware_version,
        connection_mode=instance.connection_mode,
    )

    # 8. Execute single reversible op
    t_exec = time.time()
    ev = execute_single(operation_id, ctx)
    t_exec_ms = int((time.time() - t_exec) * 1000)

    # After reconnect ops, executor advances to a fresh session; use it for final snapshot.
    transport = ctx.transport

    # 9. Final Restore Gate
    t_final = time.time()
    final_collector = BaselineCollector(transport)
    final_snap = final_collector.collect(ops_for_baseline)
    baseline_restored = recovery.final_matches_initial(final_snap)
    contradictions: list[str] = []
    if not baseline_restored:
        diff = recovery.final_diff(final_snap)
        contradictions.append(f"final baseline mismatch: {diff}")
        # attempt recovery
        rec = recovery.recover_all(transport)
        # re-check
        final_snap2 = final_collector.collect(ops_for_baseline)
        baseline_restored = recovery.final_matches_initial(final_snap2)
        if not baseline_restored:
            contradictions.append(f"recovery failed: {rec}")
        final_snap = final_snap2
    t_final_ms = int((time.time() - t_final) * 1000)
    total_ms = int((time.time() - total_start) * 1000)

    coverage = coverage_report(bundle, [ev])
    timings = {
        "baseline_ms": t_baseline_ms,
        "execute_ms": t_exec_ms,
        "final_ms": t_final_ms,
        "total_ms": total_ms,
    }
    knowledge_rev = ""
    try:
        knowledge_rev = bundle.raw.get("knowledge_revision", "")  # type: ignore
    except Exception:
        knowledge_rev = ""
    cert = build_certificate(verdict, bundle, snap.hash, final_snap.hash, baseline_restored, [ev], contradictions, coverage, knowledge_revision=knowledge_rev, timings=timings)

    out = output_path or Path.cwd() / f"{bundle.product.name.replace(' ', '_')}-{instance.firmware_version}.vetrojson"
    cert.write(out)
    # Close real/sig transport if needed
    try:
        if hasattr(transport, "raw") and hasattr(transport.raw, "close"):
            # For real hardware keep closed after cert; for sim also close
            if use_real or use_sim:
                # Keep handle open? Close to release
                pass
    except Exception:
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vetro_probe", description="Vetro Probe — Physical Validation (headless vertical slice)")
    parser.add_argument("--bundle", type=Path, help="Path to preview bundle JSON (vetro.preview-bundle.v1)")
    parser.add_argument("--operation", type=str, default="he.actuation", help="Operation id to validate (single-op mode)")
    parser.add_argument("--batch", action="store_true", help="Run planner multi-op batch (all mandatory reversible ops)")
    parser.add_argument("--max-ops", type=int, default=None, help="Limit batch to N ops (for quick smoke)")
    parser.add_argument("--output", type=Path, help="Output .vetrojson path")
    parser.add_argument("--demo", action="store_true", help="Demo mode (mock device)")
    parser.add_argument("--real", action="store_true", help="Use real HID hardware (requires HERO84 connected)")
    parser.add_argument("--sim", action="store_true", help="Use pdevemu simulator (no hardware)")
    parser.add_argument("--list-ops", action="store_true", help="List operations in bundle and exit")
    parser.add_argument("--auto", action="store_true", help="Run one-command end-to-end auto flow (state machine + miner package)")
    parser.add_argument("--auto-dir", type=Path, default=None, help="Run-state/package directory for --auto (default: vetro_auto_<ts>)")
    parser.add_argument("--label", type=str, default="auto", help="Human label for the auto run")
    parser.add_argument("--plan-dry", action="store_true", help="Dry/no-write: build the HERO84 plan (classification only) and print it. No transport reads/writes, no baselining, no execution.")
    args = parser.parse_args(argv)

    if args.list_ops:
        bundle = _load_bundle_with_fallback(args.bundle, args.operation)
        print(f"Bundle {bundle.id} v{bundle.version} hash {bundle.hash[:12]} knowledge {bundle.raw.get('knowledge_revision','')}")
        for op_id, op in bundle.operations.items():
            print(f"  {op_id}: kind={op.kind} reversible={op.reversible} readback={op.readback} reconnect={op.requires_reconnect}")
        return 0

    if args.real and args.sim:
        print("error: --real and --sim are mutually exclusive", file=sys.stderr)
        return 2

    if args.plan_dry:
        from .automation import AutoProbeRun, CLS_AUTO_REVERSIBLE
        from .bundle import production_bundle_for_hero84
        from .identity import mock_hero84_instance
        from .transport import FakeTransport

        bundle = production_bundle_for_hero84() if not (args.bundle and Path(args.bundle).is_file()) else load_bundle(Path(args.bundle))
        instance = mock_hero84_instance(firmware=bundle.firmware_branch)
        # plan_only() never touches the transport: use a throwaway fake so AutoProbeRun can be constructed.
        transport = FakeTransport(initial_state={})
        run = AutoProbeRun(bundle=bundle, transport=transport, instance=instance,
                           enumerate_fn=lambda: instance, make_transport=lambda: transport.fresh_session(),
                           run_dir=Path.cwd() / ".vetro_plan_dry")
        run.plan_only()
        print(f"=== VETRO PROBE DRY PLAN (no writes) — {bundle.product.name} / {bundle.product.vid}:{bundle.product.pid} / {bundle.family} / FW {instance.firmware_version} ===")
        for e in run.plan:
            mark = "[AUTO_REVERSIBLE]" if e["classification"] == CLS_AUTO_REVERSIBLE else f"[{e['classification']}]"
            print(f"  {mark} {e['operation']:<24} {e['why_safe'][:110]}")
        return 0

    if args.auto:
        from .automation import AutoProbeRun
        from .bundle import production_bundle_for_hero84

        bundle = production_bundle_for_hero84() if not (args.bundle and Path(args.bundle).is_file()) else load_bundle(Path(args.bundle))
        try:
            if args.real:
                from .aula_transport import AulaHidTransport
                from .identity import discover_real_instance_via_raw

                transport = AulaHidTransport.open_real(uuid=int(bundle.product.uuid) if bundle.product.uuid else None)
                instance = discover_real_instance_via_raw(transport.raw)

                def enumerate_fn():
                    try:
                        import hid  # type: ignore
                        return instance if hid.enumerate(0x372E, 0x103E) else None
                    except Exception:
                        return None

                def make_transport():
                    return AulaHidTransport.open_real(uuid=int(bundle.product.uuid) if bundle.product.uuid else None)

                fw_check = lambda inst: (inst.firmware_version == bundle.firmware_branch, f"observed {inst.firmware_version} expected {bundle.firmware_branch}")
            else:
                from .identity import mock_hero84_instance
                from .transport import FakeTransport

                initial = {p.operation_id: 1.0 for p in plan(bundle)}
                if "keyboard.polling" in initial:
                    initial["keyboard.polling"] = 3
                if "he.actuation" in initial:
                    initial["he.actuation"] = 1.0
                transport = FakeTransport(initial_state=initial, reconnect_ops={"keyboard.polling"})
                instance = mock_hero84_instance()
                enumerate_fn = lambda: instance
                make_transport = lambda: transport.fresh_session()
                fw_check = None
        except Exception as exc:
            print(f"[VetroProbe] auto FAIL: {exc}", file=sys.stderr)
            return 1

        run_dir = args.auto_dir or Path.cwd() / f"vetro_auto_{int(time.time())}"
        # Real run policy: do not weaken eligibility to fill the plan.
        block_holes = args.real
        block_e5 = args.real
        run = AutoProbeRun(bundle=bundle, transport=transport, instance=instance,
                           enumerate_fn=enumerate_fn, firmware_check=fw_check,
                           make_transport=make_transport, run_dir=run_dir, label=args.label,
                           block_knowledge_holes=block_holes, block_missing_strong_e5=block_e5)
        run.run()
        print(run.summary())
        return 0 if run.verdict in ("COMPLETE",) else 2

    try:
        if args.batch:
            out = run_batch(bundle_path=args.bundle, output_path=args.output, use_real=args.real, use_sim=args.sim, max_ops=args.max_ops)
        else:
            out = run_headless(bundle_path=args.bundle, output_path=args.output, operation_id=args.operation, demo=args.demo, use_real=args.real, use_sim=args.sim)
        print(f"[VetroProbe] Certificate written: {out}")
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        print(f"[VetroProbe] Verdict: {data['verdict']}  mandatory_core={data['coverage']['mandatory_core']}  baseline_restored={data['baseline_restored']}")
        if data.get("knowledge_revision"):
            print(f"[VetroProbe] Knowledge revision: {data['knowledge_revision']}")
        if data.get("timings"):
            print(f"[VetroProbe] Timings ms: {data['timings']}")
        for t in data["tests"]:
            print(f"  - {t['operation']}: {t['status']}  strength={t['evidence_strength']}  err={t['error']}")
        return 0 if data["verdict"] == "PASS" else 2
    except Exception as exc:
        print(f"[VetroProbe] FAIL: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
