"""Scripted experiment runner for safe fake-device UI automation."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from miner.dynamic.ui_discovery import DiscoveredControl


@dataclass
class ExperimentStep:
    action_id: str
    control_id: str
    label: str
    old_value: Any
    new_value: Any
    semantic_context: str
    step_type: str  # "experiment", "validation_point", "restore"
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentResult:
    control_id: str
    label: str
    control_type: str
    baseline_value: Any
    steps: list[ExperimentStep]
    restore_status: str  # "RESTORE_CONFIRMED", "RESTORE_UNCERTAIN", "SKIPPED"
    executed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_experiment_plan(control: DiscoveredControl) -> list[tuple[Any, str]]:
    """Generate minimal test value sequence for a given control type: (value, step_type)."""
    orig = control.current_value
    steps: list[tuple[Any, str]] = []

    if control.control_type == "boolean":
        first = not bool(orig)
        steps.append((first, "experiment"))
        steps.append((orig, "restore"))

    elif control.control_type == "enum" and control.enum_options:
        opts = [o for o in control.enum_options if str(o) != str(orig)]
        if opts:
            steps.append((opts[0], "experiment"))
        if len(opts) > 1:
            steps.append((opts[1], "experiment"))
        steps.append((orig, "restore"))

    elif control.control_type in {"numeric_slider", "per_key_numeric"}:
        min_v = control.min_value if control.min_value is not None else 0.0
        max_v = control.max_value if control.max_value is not None else 100.0
        step_v = control.step if control.step is not None and control.step > 0 else 1.0

        low = min_v + step_v
        mid = (min_v + max_v) / 2.0
        high = max_v - step_v
        val_point = min_v + (max_v - min_v) * 0.75

        test_points = [p for p in (low, mid, high) if abs(p - float(orig or 0)) > 1e-4]
        for p in test_points[:2]:
            steps.append((round(p, 2), "experiment"))
        steps.append((round(val_point, 2), "validation_point"))
        steps.append((orig, "restore"))

    elif control.control_type == "color_rgb":
        test_colors = ["#ff0000", "#00ff00", "#0000ff"]
        for c in test_colors:
            if c != orig:
                steps.append((c, "experiment"))
        steps.append((orig, "restore"))

    return steps


def run_control_experiments(page: Any, controls: list[DiscoveredControl]) -> list[ExperimentResult]:
    """Execute scripted experiments on safe controls within Playwright sandbox."""
    results: list[ExperimentResult] = []
    action_counter = 1

    for control in controls:
        if not control.is_safe_for_auto_experiment:
            results.append(
                ExperimentResult(
                    control_id=control.control_id,
                    label=control.label,
                    control_type=control.control_type,
                    baseline_value=control.current_value,
                    steps=[],
                    restore_status="SKIPPED",
                    executed=False,
                )
            )
            continue

        plan = generate_experiment_plan(control)
        if not plan:
            continue

        executed_steps: list[ExperimentStep] = []
        current_val = control.current_value

        for target_val, step_type in plan:
            action_id = f"act-{action_counter:04d}"
            action_counter += 1
            semantic = f"{control.label}:{target_val}"

            # Set context in browser page
            page.evaluate(
                """([actId, ctx]) => {
                    window.__protocolMinerCurrentActionId = actId;
                    window.__protocolMinerCurrentSemanticContext = ctx;
                }""",
                [action_id, semantic],
            )

            # Apply UI change via DOM event dispatch
            selector = control.selector
            try:
                if control.control_type == "numeric_slider":
                    page.evaluate(
                        """([sel, val]) => {
                            const el = document.querySelector(sel);
                            if (el) {
                                el.value = val;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }""",
                        [selector, str(target_val)],
                    )
                elif control.control_type == "boolean":
                    page.evaluate(
                        """([sel, checked]) => {
                            const el = document.querySelector(sel);
                            if (el) {
                                el.checked = Boolean(checked);
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }""",
                        [selector, target_val],
                    )
                elif control.control_type == "enum":
                    page.evaluate(
                        """([sel, val]) => {
                            const el = document.querySelector(sel);
                            if (el) {
                                el.value = val;
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }""",
                        [selector, str(target_val)],
                    )
                elif control.control_type == "color_rgb":
                    page.evaluate(
                        """([sel, val]) => {
                            const el = document.querySelector(sel);
                            if (el) {
                                el.value = val;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }""",
                        [selector, str(target_val)],
                    )

                page.wait_for_timeout(100)

                step = ExperimentStep(
                    action_id=action_id,
                    control_id=control.control_id,
                    label=control.label,
                    old_value=current_val,
                    new_value=target_val,
                    semantic_context=semantic,
                    step_type=step_type,
                    timestamp=time.time(),
                )
                executed_steps.append(step)
                current_val = target_val
            except Exception:
                break

        # Check restore status
        final_step = executed_steps[-1] if executed_steps else None
        restore_ok = final_step is not None and final_step.step_type == "restore"
        status_str = "RESTORE_CONFIRMED" if restore_ok else "RESTORE_UNCERTAIN"

        results.append(
            ExperimentResult(
                control_id=control.control_id,
                label=control.label,
                control_type=control.control_type,
                baseline_value=control.current_value,
                steps=executed_steps,
                restore_status=status_str,
                executed=len(executed_steps) > 0,
            )
        )

    return results
