"""Multi-submission corroboration, firmware branch analysis, and protocol family clustering."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from miner.schemas.models import ConfidenceClass, Observation


@dataclass
class CorroboratedFact:
    fact_key: str
    fact_value: Any
    corroborating_submissions: list[str]
    corroboration_count: int
    confidence: str
    firmware_versions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FirmwareBranchAnalysis:
    model_name: str
    branches: dict[str, dict[str, Any]] = field(default_factory=dict)
    contradictions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProtocolFamilyCluster:
    family_id: str
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    matching_signals: list[str]
    device_models: list[str]
    signature: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def corroborate_submissions(
    submissions: list[dict[str, Any]],
) -> tuple[list[CorroboratedFact], list[dict[str, Any]]]:
    """Aggregate multiple submission records, tracking independent corroboration and conflicts."""
    facts_map: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for sub in submissions:
        sub_id = sub.get("submission_id", "unknown_sub")
        fw_ver = sub.get("firmware_version", "unknown_fw")
        facts = sub.get("facts", {})
        for k, v in facts.items():
            facts_map[k].append({"sub_id": sub_id, "fw_ver": fw_ver, "value": v})

    corroborated: list[CorroboratedFact] = []
    contradictions: list[dict[str, Any]] = []

    for fact_key, entries in facts_map.items():
        # Group by distinct value representation
        val_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in entries:
            val_str = str(e["value"])
            val_groups[val_str].append(e)

        if len(val_groups) == 1:
            # Full agreement
            grp = list(val_groups.values())[0]
            subs = list({e["sub_id"] for e in grp})
            fws = list({e["fw_ver"] for e in grp if e["fw_ver"] != "unknown_fw"})
            count = len(subs)
            conf = ConfidenceClass.INFERRED_STRONG.value if count >= 2 else ConfidenceClass.VERIFIED_DYNAMIC_VENDOR_SOFTWARE.value
            corroborated.append(
                CorroboratedFact(
                    fact_key=fact_key,
                    fact_value=grp[0]["value"],
                    corroborating_submissions=subs,
                    corroboration_count=count,
                    confidence=conf,
                    firmware_versions=fws,
                )
            )
        else:
            # Contradiction / branch difference
            variants = []
            for val_str, grp in val_groups.items():
                subs = list({e["sub_id"] for e in grp})
                fws = list({e["fw_ver"] for e in grp if e["fw_ver"] != "unknown_fw"})
                variants.append({"value": grp[0]["value"], "submissions": subs, "firmware_versions": fws, "count": len(subs)})
            contradictions.append({
                "fact_key": fact_key,
                "reason": "disagreement across submissions / firmware versions",
                "variants": variants,
            })

    return corroborated, contradictions


def cluster_protocol_families(
    device_profiles: list[dict[str, Any]],
) -> list[ProtocolFamilyCluster]:
    """Cluster devices into protocol families using multi-signal correlation (rejecting single-signal false positives)."""
    clusters: list[ProtocolFamilyCluster] = []

    for profile in device_profiles:
        model = profile.get("model", "unknown")
        usage_page = profile.get("usage_page")
        report_ids = sorted(profile.get("report_ids", []))
        opcode_prefix = profile.get("opcode_prefix")
        packet_len = profile.get("packet_length")

        matched_cluster = None
        for cluster in clusters:
            sig = cluster.signature
            signals: list[str] = []

            # Check usage page
            if usage_page and sig.get("usage_page") == usage_page:
                signals.append("usage_page")

            # Check report IDs topology
            if report_ids and sig.get("report_ids") == report_ids:
                signals.append("report_ids")

            # Check opcode prefix
            if opcode_prefix and sig.get("opcode_prefix") == opcode_prefix:
                signals.append("opcode_prefix")

            # Check packet length
            if packet_len and sig.get("packet_length") == packet_len:
                signals.append("packet_length")

            # CRITICAL NEGATIVE CONTROL: Must have at least 2 distinct matching signals beyond generic usage page
            distinct_non_usage_signals = [s for s in signals if s != "usage_page"]
            if len(signals) >= 2 and len(distinct_non_usage_signals) >= 1:
                matched_cluster = cluster
                cluster.matching_signals = list(set(cluster.matching_signals + signals))
                if model not in cluster.device_models:
                    cluster.device_models.append(model)
                break

        if not matched_cluster:
            new_fam_id = f"fam-{len(clusters) + 1:03d}"
            clusters.append(
                ProtocolFamilyCluster(
                    family_id=new_fam_id,
                    confidence="HIGH" if opcode_prefix and report_ids else "MEDIUM",
                    matching_signals=["initial_profile"],
                    device_models=[model],
                    signature={
                        "usage_page": usage_page,
                        "report_ids": report_ids,
                        "opcode_prefix": opcode_prefix,
                        "packet_length": packet_len,
                    },
                )
            )

    return clusters
