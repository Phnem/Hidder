"""TICKET-25: acquire the `/cizhou/` Next.js app — the CZ configurator UI.

Distinct from `mchose_cz_sdk_acquire.py`, which takes `CZ_SHARED_DATA` (the code
that *builds frames*). This takes the app that *renders the UI and parses
replies* — the piece that decides whether the configurator draws anything, and
therefore the piece that has to be read before any synthetic payload can be
called schema-derived rather than invented.

## Why the closure is checkable

Next.js splits into an initial set referenced from the HTML plus lazily loaded
chunks whose filenames live in a `chunkId -> contenthash` map inside
`webpack-<hash>.js`. Taking only the HTML's list would silently miss exactly the
chunks that matter (the parser that threw lives in a lazy one). So both sources
are read and the union is fetched, and any chunk the map names but the server
does not serve is recorded as a miss rather than dropped.

Artifacts are never committed (`data/README.md`); only this manifest of facts.
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

ORIGIN = "https://www.mchose.com.cn"
APP = ORIGIN + "/cizhou/"
STATIC = ORIGIN + "/cizhou/_next/static/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) protocol-miner/ticket-25"

_HTML_ASSET = re.compile(r"/cizhou/_next/static/[A-Za-z0-9._/-]+\.(?:js|css)")
_CHUNK_MAP = re.compile(r"\{(?:\d+:\"[0-9a-f]{12,}\",?){3,}\}")
_MAP_ENTRY = re.compile(r"(\d+):\"([0-9a-f]{12,})\"")


def fetch(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read(), {
            "http_status": resp.status,
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
            "content_type": resp.headers.get("Content-Type"),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cas", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cas = Path(args.cas)
    cas.mkdir(parents=True, exist_ok=True)

    html, html_meta = fetch(APP)
    (cas / "index.html").write_bytes(html)
    text = html.decode("utf-8", "replace")

    # Trailing backslashes appear in the HTML's escaped JSON payloads; strip them
    # rather than fetching URLs that 404 for a punctuation reason.
    from_html = {m.group(0).rstrip("\\") for m in _HTML_ASSET.finditer(text)}
    print(f"assets referenced by the HTML: {len(from_html)}")

    # The lazy chunks: read the webpack runtime's own chunkId -> hash map.
    lazy: set[str] = set()
    webpack_urls = [u for u in from_html if "/chunks/webpack-" in u]
    for wu in webpack_urls:
        body, _ = fetch(ORIGIN + wu)
        wt = body.decode("utf-8", "replace")
        for m in _CHUNK_MAP.finditer(wt):
            blob = m.group(0)
            # Two maps exist: one for js chunks, one for css. Distinguish by
            # which extension the surrounding template uses, rather than by
            # guessing from the hash.
            tail = wt[m.end():m.end() + 200]
            ext = ".css" if ".css" in tail[:60] else ".js"
            sub = "css/" if ext == ".css" else "chunks/"
            for cid, h in _MAP_ENTRY.findall(blob):
                lazy.add(f"/cizhou/_next/static/{sub}{cid}.{h}{ext}")
    print(f"lazy chunks named by the webpack runtime: {len(lazy)}")

    urls = sorted(from_html | lazy)
    artifacts = []
    total = 0
    for u in urls:
        full = ORIGIN + u
        local = u.split("/cizhou/_next/static/", 1)[1].replace("/", "__")
        try:
            body, meta = fetch(full)
        except (HTTPError, URLError) as exc:
            artifacts.append({"url": full, "local_name": local, "error": str(exc)})
            continue
        (cas / local).write_bytes(body)
        total += len(body)
        sm = re.search(rb"//# sourceMappingURL=(\S+)", body[-4096:])
        artifacts.append({
            "url": full,
            "local_name": local,
            "size_bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "sourceMappingURL": sm.group(1).decode("utf-8", "replace") if sm else None,
            "source": "html" if u in from_html else "webpack_chunk_map",
            **meta,
        })
    ok = [a for a in artifacts if "sha256" in a]
    miss = [a for a in artifacts if "error" in a]
    print(f"fetched {len(ok)} artifacts, {total} bytes; {len(miss)} missing")

    doc = {
        "_what": "MCHOSE /cizhou/ Next.js configurator app, TICKET-25",
        "_why": ("This is the code that parses device replies and renders the keyboard UI. "
                 "The CZ SDK builds frames; this decides what a reply has to look like for "
                 "the configurator to draw anything."),
        "_method": ("union of the HTML's asset list and the chunkId->contenthash map inside "
                    "webpack-<hash>.js, because the parser that matters lives in a lazily "
                    "loaded chunk the HTML never names"),
        "_artifacts_not_committed": "per data/README.md only this manifest of facts is committed",
        "origin": APP,
        "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "index_sha256": hashlib.sha256(html).hexdigest(),
        "index_etag": html_meta.get("etag"),
        "artifact_count": len(ok),
        "missing_count": len(miss),
        "total_bytes": total,
        "sourcemaps_present": sum(1 for a in ok if a.get("sourceMappingURL")),
        "artifacts": artifacts,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
