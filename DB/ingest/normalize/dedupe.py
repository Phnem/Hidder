"""Conservative multi-factor product deduplication."""

import re
from typing import Optional, NamedTuple
from ingest.logging_setup import log_dedupe


class DedupeMatch(NamedTuple):
    is_match: bool
    is_ambiguous: bool
    reason: str
    confidence: float


def simplify_name(name: str) -> str:
    """Normalize string for fuzzy comparison (remove spaces, lowercase, clean symbols)."""
    return re.sub(r'[\s\-_+.]', '', name).lower()


def evaluate_product_match(
    vendor_a: str,
    name_a: str,
    url_a: Optional[str],
    vendor_b: str,
    name_b: str,
    url_b: Optional[str],
) -> DedupeMatch:
    """
    Conservatively evaluate if two product entries refer to the exact same device.
    """
    # 1. Vendors must match
    if vendor_a.strip().lower() != vendor_b.strip().lower():
        return DedupeMatch(
            is_match=False,
            is_ambiguous=False,
            reason="Different vendors",
            confidence=0.0
        )

    # 2. Exact product URL match -> definitely same product
    if url_a and url_b and url_a.strip().lower() == url_b.strip().lower():
        return DedupeMatch(
            is_match=True,
            is_ambiguous=False,
            reason=f"Exact product URL match: {url_a}",
            confidence=1.0
        )

    # 3. Normalized canonical names
    sim_a = simplify_name(name_a)
    sim_b = simplify_name(name_b)

    if sim_a == sim_b and len(sim_a) >= 2:
        return DedupeMatch(
            is_match=True,
            is_ambiguous=False,
            reason=f"Identical normalized name: '{name_a}' == '{name_b}'",
            confidence=0.95
        )

    # 4. Partial substring or version variation check
    # If one is "Hero 84 HE" and other is "Hero84", flag as ambiguous candidate rather than auto-merging
    if (sim_a in sim_b or sim_b in sim_a) and abs(len(sim_a) - len(sim_b)) <= 4:
        return DedupeMatch(
            is_match=False,
            is_ambiguous=True,
            reason=f"Possible model variant or duplicate ('{name_a}' vs '{name_b}')",
            confidence=0.60
        )

    return DedupeMatch(
        is_match=False,
        is_ambiguous=False,
        reason="Distinct product models",
        confidence=0.1
    )
