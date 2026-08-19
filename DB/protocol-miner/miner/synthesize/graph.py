"""Deterministic evidence graph serialization for review and downstream staging."""

from __future__ import annotations

import hashlib
import json

from miner.schemas.models import Observation, ProtocolCandidate


def _fact_id(kind: str, value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"fact-{hashlib.sha256(kind.encode() + b'|' + raw).hexdigest()[:20]}"


def build(artifact_sha256: str, observations: list[Observation], candidate: ProtocolCandidate) -> dict:
    nodes: list[dict] = [{"id": f"artifact:{artifact_sha256}", "type": "Artifact", "sha256": artifact_sha256}]
    edges: list[dict] = []
    facts: dict[str, dict] = {}
    for observation in observations:
        nodes.append({"id": observation.observation_id, "type": "Observation", "kind": observation.kind, "confidence": observation.confidence.value})
        edges.append({"from": f"artifact:{artifact_sha256}", "to": observation.observation_id, "type": "produced"})
        fact_id = _fact_id(observation.kind, observation.value)
        facts.setdefault(fact_id, {"id": fact_id, "type": "Fact", "kind": observation.kind, "value": observation.value})
        edges.append({"from": observation.observation_id, "to": fact_id, "type": "supports"})
    nodes.extend(facts.values())
    candidate_id = f"candidate:{artifact_sha256[:16]}"
    nodes.append({"id": candidate_id, "type": "ProtocolFamilyCandidate", "family_candidate": candidate.family_candidate})
    for evidence_ref in candidate.evidence_refs:
        edges.append({"from": evidence_ref, "to": candidate_id, "type": "synthesizes"})
    return {"schema": "peripheral.evidence-graph/1", "nodes": sorted(nodes, key=lambda item: item["id"]), "edges": sorted(edges, key=lambda item: (item["from"], item["to"], item["type"]))}
