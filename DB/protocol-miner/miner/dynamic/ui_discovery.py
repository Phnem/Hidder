"""DOM discovery and classification of interactive vendor configurator controls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from miner.dynamic.safety_filter import SafetyDecision, classify_control_safety


@dataclass
class DiscoveredControl:
    control_id: str
    selector: str
    label: str
    control_type: str
    current_value: Any
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    enum_options: list[str] | None = None
    disabled: bool = False
    tab_section: str | None = None
    safety_status: str = "SAFE"
    safety_reason: str = ""
    is_safe_for_auto_experiment: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_DOM_DISCOVERY_JS = """
(() => {
  const controls = [];
  let idCounter = 1;

  function getLabel(el) {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    if (el.getAttribute('title')) return el.getAttribute('title').trim();
    if (el.id) {
      const labelEl = document.querySelector(`label[for="${el.id}"]`);
      if (labelEl) return labelEl.innerText.trim();
    }
    const parentLabel = el.closest('label');
    if (parentLabel) return parentLabel.innerText.trim();
    const prev = el.previousElementSibling;
    if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN' || prev.tagName === 'P')) {
      return prev.innerText.trim();
    }
    return el.innerText ? el.innerText.trim() : (el.name || el.id || '');
  }

  function getSection(el) {
    const section = el.closest('section, fieldset, [role="tabpanel"], .tab-content, .card');
    if (section) {
      const heading = section.querySelector('h1, h2, h3, h4, legend, .tab-title');
      if (heading) return heading.innerText.trim();
    }
    return null;
  }

  // Sliders (range)
  document.querySelectorAll('input[type="range"]').forEach((el) => {
    const cid = el.id || `pm-slider-${idCounter++}`;
    controls.push({
      control_id: cid,
      selector: el.id ? `#${el.id}` : `input[type="range"][name="${el.name}"]`,
      label: getLabel(el),
      control_type: 'numeric_slider',
      current_value: parseFloat(el.value) || 0,
      min_value: el.min !== '' ? parseFloat(el.min) : 0,
      max_value: el.max !== '' ? parseFloat(el.max) : 100,
      step: el.step !== '' ? parseFloat(el.step) : 1,
      disabled: el.disabled,
      tab_section: getSection(el),
      class_name: el.className,
      name: el.name,
      id: el.id
    });
  });

  // Checkboxes / Toggles
  document.querySelectorAll('input[type="checkbox"]').forEach((el) => {
    const cid = el.id || `pm-chk-${idCounter++}`;
    controls.push({
      control_id: cid,
      selector: el.id ? `#${el.id}` : `input[type="checkbox"][name="${el.name}"]`,
      label: getLabel(el),
      control_type: 'boolean',
      current_value: el.checked,
      disabled: el.disabled,
      tab_section: getSection(el),
      class_name: el.className,
      name: el.name,
      id: el.id
    });
  });

  // Select dropdowns (enum)
  document.querySelectorAll('select').forEach((el) => {
    const cid = el.id || `pm-sel-${idCounter++}`;
    const options = Array.from(el.options).map(o => o.text.trim() || o.value);
    controls.push({
      control_id: cid,
      selector: el.id ? `#${el.id}` : `select[name="${el.name}"]`,
      label: getLabel(el),
      control_type: 'enum',
      current_value: el.value,
      enum_options: options,
      disabled: el.disabled,
      tab_section: getSection(el),
      class_name: el.className,
      name: el.name,
      id: el.id
    });
  });

  // Color inputs
  document.querySelectorAll('input[type="color"]').forEach((el) => {
    const cid = el.id || `pm-col-${idCounter++}`;
    controls.push({
      control_id: cid,
      selector: el.id ? `#${el.id}` : `input[type="color"][name="${el.name}"]`,
      label: getLabel(el),
      control_type: 'color_rgb',
      current_value: el.value,
      disabled: el.disabled,
      tab_section: getSection(el),
      class_name: el.className,
      name: el.name,
      id: el.id
    });
  });

  // Buttons / Actions
  document.querySelectorAll('button, input[type="button"]').forEach((el) => {
    const cid = el.id || `pm-btn-${idCounter++}`;
    const label = el.innerText.trim() || el.value || getLabel(el);
    controls.push({
      control_id: cid,
      selector: el.id ? `#${el.id}` : `button[name="${el.name}"]`,
      label: label,
      control_type: 'button_action',
      current_value: null,
      disabled: el.disabled,
      tab_section: getSection(el),
      class_name: el.className,
      name: el.name,
      id: el.id
    });
  });

  return controls;
})();
"""


def discover_controls_in_page(page: Any) -> list[DiscoveredControl]:
    """Scan the active Playwright page DOM, extracting and safety-classifying controls."""
    raw_controls = page.evaluate(_DOM_DISCOVERY_JS)
    discovered: list[DiscoveredControl] = []

    for raw in raw_controls:
        decision: SafetyDecision = classify_control_safety(raw)
        ctrl = DiscoveredControl(
            control_id=raw["control_id"],
            selector=raw.get("selector", ""),
            label=raw.get("label", ""),
            control_type=raw.get("control_type", "unknown"),
            current_value=raw.get("current_value"),
            min_value=raw.get("min_value"),
            max_value=raw.get("max_value"),
            step=raw.get("step"),
            enum_options=raw.get("enum_options"),
            disabled=bool(raw.get("disabled", False)),
            tab_section=raw.get("tab_section"),
            safety_status=decision.status.value,
            safety_reason=decision.reason,
            is_safe_for_auto_experiment=decision.is_safe_for_auto_experiment,
        )
        discovered.append(ctrl)

    return discovered
