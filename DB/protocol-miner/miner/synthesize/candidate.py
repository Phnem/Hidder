"""Synthesize review candidates without promoting unverified write semantics."""

from __future__ import annotations

import json
from collections import defaultdict

from miner.schemas.models import Observation, ProtocolCandidate


def _entry(observation: Observation) -> dict:
    return {**observation.value, "evidence_refs": [observation.observation_id], "confidence": observation.confidence.value}


def _unique(observations: list[Observation]) -> list[dict]:
    result: list[dict] = []
    known: set[str] = set()
    for observation in observations:
        value = _entry(observation)
        key = json.dumps({key: value[key] for key in value if key not in {"evidence_refs", "confidence"}}, sort_keys=True)
        if key not in known:
            known.add(key)
            result.append(value)
    return result


def synthesize(observations: list[Observation]) -> tuple[ProtocolCandidate, list[dict], list[dict], str]:
    """Return candidate, contradictions, future validation plan, and review status."""
    identity = _unique([item for item in observations if item.kind == "identity.vid_pid"])
    topology = _unique([item for item in observations if item.kind.startswith("topology.")])
    packets = [item for item in observations if item.kind == "protocol.direct_packet_literal"]
    builders = [item for item in observations if item.kind == "protocol.buffer_builder"]
    commands = {f"packet_{index + 1}": {**_entry(item), "safe_for_production": False} for index, item in enumerate(packets)}
    commands.update({f"builder_{index + 1}": {**_entry(item), "safe_for_production": False} for index, item in enumerate(builders)})
    dynamic_calls = [item for item in observations if item.kind == "dynamic.webhid_call" and item.value.get("method") in {"sendReport", "sendFeatureReport"}]
    for command in commands.values():
        matched = [item.observation_id for item in dynamic_calls if item.value.get("report_id") == command.get("report_id")]
        if matched:
            command["dynamic_evidence_refs"] = matched
    capabilities = {
        item.value["semantic_candidate"]: {"evidence_refs": [item.observation_id], "raw_function": item.value["function"], "confidence": item.confidence.value}
        for item in builders if "semantic_candidate" in item.value
    }
    dangerous = [{**_entry(item), "dangerous_candidate": True} for item in observations if item.kind == "protocol.dangerous_command_candidate"]

    conflicts: list[dict] = []
    grouped: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for item in builders:
        semantic = item.value.get("semantic_candidate")
        if semantic:
            grouped[(str(semantic), str(item.value["function"]))].append(item)
    for (semantic, function), entries in grouped.items():
        signatures = {(item.value.get("report_id"), json.dumps(item.value.get("field_writes", []), sort_keys=True)) for item in entries}
        if len(signatures) > 1:
            conflicts.append({
                "field": f"command_layout.{semantic}", "function": function,
                "values": [{"report_id": item.value.get("report_id"), "field_writes": item.value.get("field_writes", []), "evidence_refs": [item.observation_id]} for item in entries],
                "reason": "same semantic function has conflicting static layouts; firmware/model branch needs review",
            })

    unknowns: list[str] = []
    if not identity:
        unknowns.append("No contextual VID/PID mapping was found.")
    if not topology:
        unknowns.append("No HID/USB topology was found.")
    if not commands:
        unknowns.append("No packet builder or direct packet literal was proven.")
    unknowns.extend([
        "Read semantics, response validation, persistence, rollback, cadence, and safe write behavior are unknown unless separately evidenced.",
        "No observation is hardware-verified; Protocol Miner never accesses a real HID device.",
    ])
    plan: list[dict] = []
    if topology:
        plan.append({"need": "HID report descriptor and interface topology", "reason": "confirm report IDs, payload lengths, and usage collections"})
    for item in builders:
        if "semantic_candidate" in item.value:
            plan.append({"need": f"official utility read-back for {item.value['semantic_candidate']}", "reason": "validate persistence and response semantics without raw probing"})
    if not plan:
        plan.append({"need": "official vendor configurator, utility, or driver artifact", "reason": "obtain stronger static protocol evidence"})

    if any(item.kind == "ecosystem.vial" for item in observations):
        family = "vial"
    elif any(item.kind == "ecosystem.via_qmk" for item in observations):
        family = "via-qmk"
    elif any(item.kind == "ecosystem.qmk" for item in observations):
        family = "qmk"
    else:
        family = None
    candidate = ProtocolCandidate(
        family_candidate=family, identity=identity, topology=topology, capabilities=capabilities,
        commands=commands, dangerous_commands=dangerous, evidence_refs=[item.observation_id for item in observations],
        contradictions=conflicts, unknowns=unknowns,
    )
    if conflicts:
        status = "CONTRADICTED"
    elif any("semantic_candidate" in item.value for item in builders):
        status = "WRITE_SEMANTICS_CANDIDATE"
    elif commands:
        status = "PROTOCOL_CANDIDATE"
    elif topology:
        status = "TOPOLOGY_CANDIDATE"
    elif identity:
        status = "IDENTITY_ONLY"
    else:
        status = "NO_TECHNICAL_EVIDENCE"
    return candidate, conflicts, plan, status
