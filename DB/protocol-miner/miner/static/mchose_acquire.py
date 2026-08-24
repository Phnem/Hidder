"""TICKET-23 step 1-2: acquire the MCHOSE M HUB Web asset graph with provenance.

Walks the Vite module graph of https://www.mchose.com.cn/ starting from
index.html, following BOTH static `import"./x.js"` specifiers and dynamic
`import("./x.js")` calls, and records for every artifact: sha256, size,
HTTP status, ETag, Last-Modified, and the wall-clock time it was fetched.

Why a recursive crawl rather than a grep of the entry bundle: grepping
`main-*.js` for asset paths yields 6 references while index.html alone
modulepreloads 9, and the app clearly lazy-loads more. A grep would silently
under-report the corpus, and TICKET-23's acceptance criterion is that the
graph is complete, not that it is large.

Why this does NOT replace the live drive-through: a chunk whose path is
assembled at runtime from pieces is invisible to any static walk. This tool
produces the static closure; `mchose_live_assets.py` produces the observed
set from a real browser session, and TICKET-23 requires the two to be
compared rather than either one trusted alone.

The blobs are written OUTSIDE the repository (scratchpad), by design:
`data/README.md` forbids committing vendor artifacts anywhere in this
repository. What the repo gets is the manifest -- hashes and provenance,
which is evidence about the artifacts rather than the artifacts.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ORIGIN = "https://www.mchose.com.cn"
ENTRY = f"{ORIGIN}/"

# Vite emits, in built chunks:
#   import"./chunk-x.js"          (bare side-effect import)
#   import{a}from"./chunk-x.js"   (named)
#   import("./chunk-x.js")        (dynamic, often via __vitePreload)
# and sprinkles plain "assets/..." string literals for preload manifests.
# Resolved RELATIVE to the importing chunk -- these are real ES specifiers.
_SPEC_PATTERNS = (
    re.compile(r'import\s*\(\s*["\']([^"\']+\.js)["\']'),
    re.compile(r'from\s*["\']([^"\']+\.js)["\']'),
    re.compile(r'import\s*["\']([^"\']+\.js)["\']'),
)

# Resolved relative to the SITE ROOT. Vite's preload manifest carries bare
# `assets/x.js` strings; joining those against a chunk that already lives in
# /assets/ yields /assets/assets/x.js, which 404s. Getting this wrong is not
# cosmetic -- a 404 for a path that also appears as a real relative import
# looks like noise, but a 404 for a chunk reachable ONLY through the preload
# manifest would be a silently missing artifact in a corpus whose whole
# acceptance criterion is completeness.
_ROOT_PATTERNS = (
    re.compile(r'["\'](?:\./)?(assets/[A-Za-z0-9_.$-]+\.(?:js|css))["\']'),
)

_HTML_ASSET = re.compile(r'(?:src|href)=["\']([^"\']+\.(?:js|css))["\']')

# The bundle logs its own build identity; capturing it turns "which version
# is this" from a hash comparison into a fact the vendor states out loud.
# AULA had no such field and its redeploys were caught only after the fact.
_SELF_VERSION = re.compile(r'console\.info\(\s*"([^"]*版本号[^"]*)"\s*,\s*"([^"]+)"')
_SELF_BUILT_AT = re.compile(r'console\.info\(\s*"([^"]*提交时间[^"]*)"\s*,\s*"([^"]+)"')


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_same_origin(url: str) -> bool:
    return urlparse(url).netloc == urlparse(ORIGIN).netloc


def fetch(session: requests.Session, url: str, timeout: int = 60) -> dict:
    """One artifact, with everything needed to prove what it was and when."""
    fetched_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    try:
        r = session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        return {"url": url, "fetched_at": fetched_at, "error": repr(exc)}
    body = r.content
    return {
        "url": url,
        "fetched_at": fetched_at,
        "http_status": r.status_code,
        "size_bytes": len(body),
        "sha256": _sha256(body),
        "etag": r.headers.get("ETag"),
        "last_modified": r.headers.get("Last-Modified"),
        "content_type": r.headers.get("Content-Type"),
        "_body": body,
    }


def specifiers(text: str, is_html: bool) -> tuple[set[str], set[str]]:
    """(relative-to-importer, relative-to-site-root) specifier sets."""
    rel: set[str] = set()
    root: set[str] = set()
    if is_html:
        root.update(s.lstrip("/") for s in _HTML_ASSET.findall(text))
    for pat in _SPEC_PATTERNS:
        rel.update(pat.findall(text))
    for pat in _ROOT_PATTERNS:
        root.update(pat.findall(text))
    return rel, root


def crawl(out_dir: Path) -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    blobs = out_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    seen: dict[str, dict] = {}
    queue = [ENTRY]
    self_reported: dict[str, str] = {}

    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        rec = fetch(session, url)
        body = rec.pop("_body", b"")
        seen[url] = rec
        if rec.get("error") or rec.get("http_status") != 200:
            print(f"  [skip] {url} -> {rec.get('http_status') or rec.get('error')}")
            continue

        name = Path(urlparse(url).path).name or "index.html"
        (blobs / name).write_bytes(body)
        rec["local_name"] = name

        text = body.decode("utf-8", errors="replace")
        is_html = url == ENTRY or name.endswith(".html")
        print(f"  [ok] {name:<40} {rec['size_bytes']:>9} bytes  {rec['sha256'][:12]}")

        for pat, key in ((_SELF_VERSION, "version"), (_SELF_BUILT_AT, "built_at")):
            m = pat.search(text)
            if m and key not in self_reported:
                self_reported[key] = m.group(2)
                self_reported[f"{key}_label"] = m.group(1)

        if name.endswith(".css"):
            continue
        rel, root = specifiers(text, is_html)
        nexts = [urljoin(url, s) for s in rel] + [urljoin(ORIGIN + "/", s) for s in root]
        for nxt in nexts:
            if _is_same_origin(nxt) and nxt not in seen and nxt not in queue:
                queue.append(nxt)

    ok = [r for r in seen.values() if r.get("http_status") == 200]
    manifest = {
        "_what": "MCHOSE M HUB Web static asset closure, TICKET-23",
        "_method": (
            "recursive walk of static+dynamic import specifiers from index.html; "
            "does NOT include chunks whose path is assembled at runtime -- compare "
            "against the live-session observed set before calling the graph complete"
        ),
        "origin": ORIGIN,
        "entry": ENTRY,
        "crawled_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "self_reported_build": self_reported,
        "artifact_count": len(ok),
        "total_bytes": sum(r["size_bytes"] for r in ok),
        "artifacts": sorted(seen.values(), key=lambda r: r["url"]),
    }
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="scratchpad dir for blobs (NOT the repo)")
    ap.add_argument("--manifest", required=True, help="manifest path (repo-safe: hashes only)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    print(f"crawling {ENTRY} -> {out_dir}")
    manifest = crawl(out_dir)

    body = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    # The manifest hashes itself so a later run can prove the record was not
    # edited after the fact (playbook §1.3 step 3).
    digest = _sha256(body)
    manifest["manifest_sha256"] = digest
    mpath = Path(args.manifest)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"artifacts : {manifest['artifact_count']}")
    print(f"bytes     : {manifest['total_bytes']:,}")
    print(f"build     : {manifest['self_reported_build']}")
    print(f"manifest  : {mpath}  (sha256 {digest[:16]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
