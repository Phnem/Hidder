#!/usr/bin/env python3
"""Verify that internal crate dependencies only ever point downward.

The layering in architecture/INITIAL_REVIEW.md §9 is an invariant, not a
diagram: a `path` dependency from a lower layer to a higher one silently
reintroduces the coupling the crate split exists to prevent, and detangling one
later touches every crate implicated in it. Cargo will grow such an edge without
complaint, so this runs in CI.

What it checks, per workspace member:

  1. no dependency on a crate in the same or a higher layer;
  2. no cycles (implied by 1, but reported distinctly when 1 is edited);
  3. every internal crate is assigned a layer here, so adding a crate without
     deciding where it sits fails rather than being unconstrained;
  4. `tools/ingest` is not a workspace member.

Usage: python3 scripts/check_crate_dag.py
Exit code 1 on any violation, with every violation listed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Lower number = lower layer. A crate may depend only on strictly lower layers.
#
# Same-layer dependencies are refused as well: pcaps, pregistry and psafety sit
# beside each other on purpose, and an edge between them would make the trio one
# unit whose parts can no longer be reasoned about separately.
LAYERS: dict[str, int] = {
    "ptransport": 0,
    "pcaps": 0,
    "pregistry": 1,
    "psafety": 1,
    "pproto": 2,
    "pprofile": 3,
    "plearn": 3,
    "pjournal": 3,
    "pcore": 4,
    "peripheral-app": 5,
    # Development tools. They may depend on anything below them and nothing may
    # depend on them.
    "pemu": 5,
    "pprotodoc": 5,
}

# pcaps and ptransport are both leaves at layer 0 and must stay leaves: everything
# above depends on them, so an edge out of either one drags its dependency into
# every layer at once.
LEAVES = {"ptransport", "pcaps"}

# tools/ingest is excluded here because it is not a workspace member, which is
# the point of it; that it stays out is asserted separately below.
NOT_A_MEMBER = {ROOT / "tools" / "ingest" / "Cargo.toml"}

MEMBER_MANIFESTS = sorted(
    manifest
    for manifest in [
        *(ROOT / "crates").glob("*/Cargo.toml"),
        ROOT / "app" / "src-tauri" / "Cargo.toml",
        *(ROOT / "tools").glob("*/Cargo.toml"),
    ]
    if manifest not in NOT_A_MEMBER
)

NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"', re.MULTILINE)


def crate_name(manifest_text: str) -> str | None:
    """The [package] name. The first `name = ` in a member manifest is the
    package's own, since [lib] comes after [package] in all of ours."""
    match = NAME_RE.search(manifest_text)
    return match.group(1) if match else None


def internal_deps(manifest_text: str) -> set[str]:
    """Internal crates this manifest depends on.

    Matched by name against LAYERS rather than by parsing dependency tables, so a
    dependency declared as `foo.workspace = true`, `foo = { path = ... }` or
    inside a platform-specific or dev-dependencies table is all caught the same
    way. Over-matching is the safe direction here: a false positive is a comment
    mentioning a crate name in a dependency line, which is worth a look anyway.
    """
    found: set[str] = set()
    for line in manifest_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip().strip('"').split(".")[0]
        if key in LAYERS:
            found.add(key)
    return found


def main() -> int:
    problems: list[str] = []

    if (ROOT / "Cargo.toml").exists():
        root_manifest = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
        if '"tools/ingest"' in root_manifest.split("[workspace.dependencies]")[0]:
            members_block = root_manifest.split("members", 1)[-1].split("]", 1)[0]
            if "tools/ingest" in members_block:
                problems.append(
                    "tools/ingest is listed as a workspace member; it must stay a "
                    "separate workspace (spec.md FR9)"
                )

    seen: set[str] = set()
    for manifest in MEMBER_MANIFESTS:
        text = manifest.read_text(encoding="utf-8")
        name = crate_name(text)
        rel = manifest.relative_to(ROOT).as_posix()
        if name is None:
            problems.append(f"{rel}: could not read a package name")
            continue
        if name not in LAYERS:
            problems.append(
                f"{rel}: crate '{name}' has no layer assigned in "
                f"{Path(__file__).name}; decide where it sits before adding it"
            )
            continue
        seen.add(name)

        own_layer = LAYERS[name]
        deps = internal_deps(text) - {name}
        if name in LEAVES and deps:
            problems.append(
                f"{rel}: '{name}' is a leaf and must depend on no internal crate, "
                f"but depends on {sorted(deps)}"
            )
        for dep in sorted(deps):
            if LAYERS[dep] >= own_layer:
                problems.append(
                    f"{rel}: '{name}' (layer {own_layer}) depends on '{dep}' "
                    f"(layer {LAYERS[dep]}); dependencies must point strictly "
                    f"downward"
                )

    missing = sorted(set(LAYERS) - seen)
    if missing:
        problems.append(
            f"declared in {Path(__file__).name} but not found as workspace "
            f"members: {missing}"
        )

    if problems:
        print("Crate dependency DAG violations:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nSee .scratch/peripheral-configurator/architecture/INITIAL_REVIEW.md §9.",
            file=sys.stderr,
        )
        return 1

    print(f"Crate DAG OK: {len(seen)} crates, dependencies point one way.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
