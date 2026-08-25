"""TICKET-25 consolidation, item 5: what the observed CZ offsets support.

This is NOT a parameter sweep. No parameter was changed, because the CZ
configurator does not render its controls against synthetic data, so there was
nothing to move. What the capture does contain is the client's own addressing:
it reads large regions in fixed chunks, repeatedly, and the offsets it chooses
are observable.

That supports exactly two kinds of claim and no others:

*   **chunk size** -- the step between consecutive reads inside one region.
*   **record stride** -- the step between the starts of successive regions,
    IF and only if enough successive regions were observed. One gap is a
    difference; a stride needs several, and this module refuses to report one
    from fewer than four regions.

It supports NO claim about what any byte at any offset means. Offsets are where
the client looked, not what it found -- and it found only zeros this harness
supplied.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

MIN_REGIONS_FOR_STRIDE = 4


def decode(payload_hex: str) -> tuple[int, int, int] | None:
    """(command, offset, size) for a CZ request; re-derived, never read from corpus."""
    try:
        raw = bytes.fromhex(payload_hex or "")
    except ValueError:
        return None
    if len(raw) < 8 or raw[0] != 0x55:
        return None
    return raw[1], raw[5] | (raw[6] << 8), raw[4]


def analyse(frames: list[dict]) -> dict:
    per_cmd: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for f in frames:
        d = decode(f.get("payload_hex") or "")
        if d:
            per_cmd[d[0]].append((d[1], d[2]))

    out = {}
    for cmd, pairs in sorted(per_cmd.items()):
        offsets = sorted({o for o, _ in pairs})
        sizes = sorted({s for _, s in pairs})
        gaps = [b - a for a, b in zip(offsets, offsets[1:])]
        gap_counts = collections.Counter(gaps)
        chunk = gap_counts.most_common(1)[0][0] if gap_counts else None

        # A region boundary is a gap larger than the chunk step. Regions are only
        # meaningful if we saw several of them.
        region_starts = [offsets[0]] if offsets else []
        for a, b in zip(offsets, offsets[1:]):
            if chunk is not None and (b - a) > chunk:
                region_starts.append(b)
        region_gaps = [b - a for a, b in zip(region_starts, region_starts[1:])]
        stride = None
        stride_note = (
            f"withheld: {len(region_starts)} region(s) observed, "
            f"{MIN_REGIONS_FOR_STRIDE} required -- a stride from fewer is a difference, not a stride"
        )
        if len(region_starts) >= MIN_REGIONS_FOR_STRIDE and region_gaps:
            uniq = set(region_gaps)
            if len(uniq) == 1:
                stride = region_gaps[0]
                stride_note = (
                    f"{len(region_gaps)} consecutive region gaps, all equal; "
                    "derived from observed offsets only"
                )
            else:
                stride_note = f"withheld: region gaps are not constant: {sorted(uniq)}"

        out[f"cmd_{cmd:#04x}"] = {
            "frames": len(pairs),
            "distinct_offsets": len(offsets),
            "offset_min": offsets[0] if offsets else None,
            "offset_max": offsets[-1] if offsets else None,
            "sizes_seen": sizes,
            "chunk_step": chunk,
            "chunk_step_support": gap_counts.get(chunk, 0) if chunk is not None else 0,
            "regions_observed": len(region_starts),
            "region_starts": region_starts[:24],
            "record_stride": stride,
            "record_stride_note": stride_note,
            "semantics": None,
            "semantics_note": (
                "offsets say where the client looked, not what is there; every reply in this "
                "capture was synthetic_from_vendor_schema, so no byte meaning is supported"
            ),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True, help="oracle jsonl, or a ui_walk inventory")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    p = Path(args.frames)
    frames: list[dict] = []
    if p.suffix == ".jsonl":
        frames = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    else:
        doc = json.loads(p.read_text(encoding="utf-8"))
        for lst in (doc.get("by_action") or {}).values():
            frames.extend(lst)

    result = analyse(frames)
    doc = {
        "_what": "CZ address map as the observed offsets support it, TICKET-25 item 5",
        "_not_a_sweep": (
            "No parameter was varied. The CZ configurator does not render controls against "
            "synthetic data, so there was nothing to move. This reports the client's own "
            "addressing and withholds any stride not supported by several regions."
        ),
        "_source": str(p),
        "frames_considered": len(frames),
        "per_command": result,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    for k, v in result.items():
        print(f"  {k}: frames={v['frames']:<4} offsets={v['distinct_offsets']:<4} "
              f"chunk={v['chunk_step']} regions={v['regions_observed']} "
              f"stride={v['record_stride']}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
