"""TICKET-23: acquire the MCHOSE config centre -- the device knowledge that is
NOT in the JS bundle.

Discovered by the live drive-through (`mchose_live_assets.py`), not by reading
the bundle: M HUB Web fetches its device catalogue, per-device presets and
firmware manifests from `cdn.mchose.com.cn/configCenter/` at runtime. A static
walk of the module graph cannot see any of it, and the bundle-only view of the
catalogue (65 identity strings, no usagePage) is both incomplete and weaker than
what the config centre states outright.

Two facts about this layer that matter for provenance:

*   **It is versioned independently of the bundle.** `global.json` carries a
    `version` map whose values are content hashes per resource, plus `webVersion`
    / `pcVersion` / `rendererVersion` blocks with real git tags and commit
    hashes. So "which version of MCHOSE are we looking at" has *several* answers
    and they move separately. Recording only the bundle hash would be recording
    one of them.

*   **The payloads are ZIP-in-base64.** Each config is
    `{"__compressBase64__": "<base64 of a ZIP whose single entry is data.json>"}`.
    Both the wrapper and the decoded content are hashed here: the wrapper is what
    the server sent, the content is what the app acts on, and a change to either
    is a change worth seeing.

Blobs go to the scratchpad, never the repository (`data/README.md`).
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import io
import json
import zipfile
from pathlib import Path

import requests

CDN = "https://cdn.mchose.com.cn"
GLOBAL_JSON = f"{CDN}/configCenter/config/global.json"
CUSTOM = f"{CDN}/configCenter/custom"

# Observed being fetched by the real app on 2026-08-24. `global.json`'s own
# `version` map names more resources than the app happened to request in one
# unauthenticated session (no device attached); those are listed but not
# assumed to live at the same path.
OBSERVED_CUSTOM = ("cardList", "keyboardConfig", "keyboardPreset", "mouseConfig", "newMouseConfig")


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def decode_compressed(payload: dict) -> tuple[object, str]:
    """`{"__compressBase64__": ...}` -> (parsed data.json, sha256 of raw bytes)."""
    blob = base64.b64decode(payload["__compressBase64__"])
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        if len(names) != 1:
            raise ValueError(f"expected one zip entry, got {names}")
        raw = z.read(names[0])
    return json.loads(raw.decode("utf-8")), _sha256(raw)


def fetch(session: requests.Session, url: str) -> tuple[dict, bytes]:
    at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    r = session.get(url, timeout=60)
    body = r.content
    return (
        {
            "url": url,
            "fetched_at": at,
            "http_status": r.status_code,
            "size_bytes": len(body),
            "sha256_wire": _sha256(body),
            "etag": r.headers.get("ETag"),
            "last_modified": r.headers.get("Last-Modified"),
        },
        body,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="scratchpad dir for decoded blobs")
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    records: list[dict] = []

    rec, body = fetch(session, GLOBAL_JSON)
    # `global.json` is compressed the same way the custom configs are. The
    # loader script embeds an already-decoded copy as `activeValue`, which is
    # why reading the loader looks like reading the config -- but the file the
    # app actually fetches is the wrapped one, and that is what gets hashed.
    glob, glob_sha = decode_compressed(json.loads(body.decode("utf-8")))
    rec["sha256_decoded"] = glob_sha
    rec["role"] = "global config; carries the version map and build identities"
    records.append(rec)
    (out / "global.json").write_bytes(body)
    (out / "global.decoded.json").write_text(
        json.dumps(glob, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    versions = glob.get("version", {})
    for name in OBSERVED_CUSTOM:
        h = versions.get(name)
        url = f"{CUSTOM}/{name}.json" + (f"?hash={h}" if h else "")
        rec, body = fetch(session, url)
        rec["config_center_hash"] = h
        try:
            data, content_sha = decode_compressed(json.loads(body.decode("utf-8")))
            rec["sha256_decoded"] = content_sha
            rec["decoded_entries"] = len(data) if hasattr(data, "__len__") else None
            (out / f"{name}.decoded.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            rec["decode_error"] = repr(exc)
        records.append(rec)
        print(f"  {name:<16} {rec['http_status']} wire={rec['size_bytes']:>7}  "
              f"entries={rec.get('decoded_entries')}")

    # Resources global.json names but that this session did not observe being
    # fetched. Recorded as named-but-not-retrieved rather than guessed at: the
    # path for these is not established, and inventing one would be exactly the
    # sort of plausible-looking fabrication the playbook warns about.
    named_not_fetched = sorted(set(versions) - set(OBSERVED_CUSTOM))

    manifest = {
        "_what": "MCHOSE config centre acquisition, TICKET-23",
        "_why": "device knowledge served at runtime; invisible to a static bundle walk",
        "acquired_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "build_identities": {
            k: glob.get(k) for k in ("webVersion", "pcVersion", "rendererVersion") if k in glob
        },
        "config_center_version_map": versions,
        "named_in_version_map_but_not_retrieved": named_not_fetched,
        "artifacts": records,
    }
    body = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    manifest["manifest_sha256"] = _sha256(body)
    mp = Path(args.manifest)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"build identities : {json.dumps(manifest['build_identities'], ensure_ascii=False)[:200]}")
    print(f"named-not-fetched: {named_not_fetched}")
    print(f"-> {mp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
