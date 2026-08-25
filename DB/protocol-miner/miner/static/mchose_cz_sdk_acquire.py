"""TICKET-25 consolidation: acquire the CZ SDK, the fifth MCHOSE code base.

The keyboard configurator for the CZ family is not in the M HUB bundle at all.
It is a separate webpack build served from

    https://www.mchose.com.cn/cizhou/CZ_SHARED_DATA/

whose own `asset-manifest.json` enumerates every chunk, including one
`default-keys-*.js` per product. That manifest is the acquisition boundary: the
closure is the vendor's own list, not a crawl of whatever happened to load, so
"did we get everything" has an answer rather than an estimate.

Produces a CAS + a provenance manifest (sha256, ETag, Last-Modified, size,
fetch time). Per `data/README.md` the artifacts themselves are never committed;
only the manifest of facts about them is.

Sourcemaps are probed for explicitly and recorded either way. An absent
sourcemap is a fact about the vendor's build worth writing down, and a present
one changes what the static lane can claim.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

BASE = "https://www.mchose.com.cn/cizhou/CZ_SHARED_DATA/"
MANIFEST_URL = BASE + "asset-manifest.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) protocol-miner/ticket-25"


def fetch(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
        return body, {
            "http_status": resp.status,
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
            "content_type": resp.headers.get("Content-Type"),
        }


def collect_asset_names(manifest: dict) -> list[str]:
    """Every string value in the manifest that looks like a chunk filename.

    The manifest mixes a flat logical-name -> hashed-name map with a nested
    `entrypoints` structure, so this walks it rather than assuming a shape.
    """
    names: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, str):
            if re.fullmatch(r"[\w.\-]+\.(js|css|json|txt|wasm)", node):
                names.add(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(manifest)
    return sorted(names)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cas", required=True, help="directory for the fetched artifacts")
    ap.add_argument("--out", required=True, help="provenance manifest path")
    args = ap.parse_args()

    cas = Path(args.cas)
    cas.mkdir(parents=True, exist_ok=True)

    raw, meta = fetch(MANIFEST_URL)
    manifest = json.loads(raw.decode("utf-8"))
    (cas / "asset-manifest.json").write_bytes(raw)

    names = collect_asset_names(manifest)
    # loader.js is the entry the host page actually requests and is NOT listed in
    # the manifest it serves, so it is added explicitly rather than silently
    # missed by trusting the manifest to be self-describing.
    if "loader.js" not in names:
        names.append("loader.js")

    print(f"assets named by the vendor's own manifest: {len(names)}")

    artifacts = []
    total = 0
    for name in names:
        url = BASE + name
        try:
            body, m = fetch(url)
        except (HTTPError, URLError) as exc:
            artifacts.append({"url": url, "local_name": name, "error": str(exc)})
            print(f"  MISS {name}: {exc}")
            continue
        (cas / name).write_bytes(body)
        total += len(body)
        sm = None
        mm = re.search(rb"//# sourceMappingURL=(\S+)", body[-4096:])
        if mm:
            sm = mm.group(1).decode("utf-8", "replace")
        artifacts.append({
            "url": url,
            "local_name": name,
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "sourceMappingURL": sm,
            **m,
        })
    print(f"fetched {sum(1 for a in artifacts if 'sha256' in a)} artifacts, {total} bytes")

    with_maps = [a for a in artifacts if a.get("sourceMappingURL")]
    doc = {
        "_what": "MCHOSE CZ SDK (CZ_SHARED_DATA) static corpus, TICKET-25 consolidation",
        "_why": ("The CZ keyboard configurator is a SEPARATE webpack build from the M HUB "
                 "bundle and from the four transports the static lane already separates. "
                 "It is the code that builds the frames a CZ keyboard actually receives."),
        "_method": ("closure taken from the vendor's own asset-manifest.json, not from a "
                    "crawl of what happened to load, so completeness is checkable"),
        "_artifacts_not_committed": "per data/README.md only this manifest of facts is committed",
        "origin": BASE,
        "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "artifact_count": len(artifacts),
        "total_bytes": total,
        "sourcemaps_present": len(with_maps),
        "artifacts": artifacts,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"sourcemaps referenced: {len(with_maps)}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
