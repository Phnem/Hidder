"""TICKET-23 step 5: the leakage gate for the MCHOSE corpus (playbook v4 §1.4).

A blind-inference corpus must not contain the answers. The failure this
prevents is not a person cheating; it is a dataset builder quietly copying a
vendor's own field name into the corpus, after which every "the engine
recovered the semantics" claim is unfalsifiable.

The rule is mechanical and it is a CI gate, not a review item: if a term from
the semantics vocabulary appears in an artifact destined for the corpus, the
build fails.

Two design points worth stating, both learned from `aula-bytech`:

*   **The gate is tested for its ability to FAIL.** A gate that cannot fail
    proves nothing when it passes, and this repository already shipped one
    check that was vacuously true for empty payloads
    (`docs/hardware/aula-bytech-exchange-014-scope-byte-discriminating.md`).
    `test_the_gate_can_fail` is therefore not ceremony.

*   **Provenance/manifest files are exempt by construction, not by accident.**
    A manifest's whole job is to record where an artifact came from, and it may
    legitimately quote a URL containing `firmware`. Exemption is by explicit
    path, so adding one is a visible decision.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_DB = Path(__file__).resolve().parents[2]
_MCHOSE = _DB / "reports" / "protocol_knowledge" / "mchose"

# Terms that name SEMANTICS. A corpus that carries these is answering the
# question the inference engine is supposed to be asked.
#
# Deliberately includes both protocol vocabulary and this vendor's own
# identifiers, because "the vendor called it that" is exactly how a leak gets
# in while looking like documentation.
FORBIDDEN = (
    r"checksum",
    r"\bcrc\b",
    r"actuation",
    r"rapid[_ ]?trigger",
    r"deadzone",
    r"dead[_ ]?zone",
    r"polling[_ ]?rate",
    r"key[_ ]?travel",
    r"factory[_ ]?reset",
    r"\bopcode\b",
    r"sub[_ ]?opcode",
    r"payload[_ ]?codec",
    r"webdriverEnum",
    r"keytype[_ ]?feature",
)

_PATTERN = re.compile("|".join(FORBIDDEN), re.IGNORECASE)

# Acquisition artifacts record PROVENANCE, not corpus content: a manifest that
# quotes a CDN path containing "firmware" is doing its job. The corpus itself
# lives elsewhere and is what this gate guards.
EXEMPT_DIRS = {"acquisition"}


def _corpus_files() -> list[Path]:
    if not _MCHOSE.exists():
        return []
    out = []
    for p in _MCHOSE.rglob("*"):
        if not p.is_file() or p.suffix not in {".json", ".jsonl", ".txt", ".csv"}:
            continue
        if EXEMPT_DIRS & set(p.relative_to(_MCHOSE).parts):
            continue
        out.append(p)
    return out


def test_no_semantics_vocabulary_reaches_the_mchose_corpus():
    offenders: list[str] = []
    for p in _corpus_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in _PATTERN.finditer(text):
            offenders.append(f"{p.relative_to(_MCHOSE)}: {m.group(0)!r} at {m.start()}")
            break
    assert not offenders, (
        "semantics vocabulary leaked into the MCHOSE corpus; a blind run over it "
        "would be unfalsifiable:\n  " + "\n  ".join(offenders)
    )


def test_the_gate_can_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The gate must reject a deliberately dirty file.

    Without this, a gate that silently matched nothing -- wrong root, wrong
    suffixes, a regex typo -- would read as a clean corpus forever.
    """
    dirty = tmp_path / "corpus" / "observations.jsonl"
    dirty.parent.mkdir(parents=True)
    dirty.write_text(json.dumps({"note": "byte 5 is the actuation scope"}), encoding="utf-8")

    monkeypatch.setattr(__import__(__name__), "_MCHOSE", tmp_path)
    found = [p for p in _corpus_files()]
    assert found, "the collector found nothing to check, so the gate is vacuous"
    assert any(_PATTERN.search(p.read_text(encoding="utf-8")) for p in found)


def test_exemption_is_by_explicit_path_not_by_luck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An exempt directory is skipped; a sibling that is not exempt is not."""
    (tmp_path / "acquisition").mkdir()
    (tmp_path / "acquisition" / "m.json").write_text('{"u":"a/firmware/checksum.bin"}', encoding="utf-8")
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "c.json").write_text('{"u":"clean"}', encoding="utf-8")

    monkeypatch.setattr(__import__(__name__), "_MCHOSE", tmp_path)
    names = {p.name for p in _corpus_files()}
    assert names == {"c.json"}, f"exemption misapplied: {names}"
