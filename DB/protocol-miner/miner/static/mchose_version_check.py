"""TICKET-23 step 6: has MCHOSE redeployed since our corpus was taken?

Called at the start of any lane run. `aula-bytech` redeployed twice inside six
days and both times it was noticed only afterwards, when a finding had already
been written against a build that no longer existed. MCHOSE hands us the answer
for free -- the bundle logs its own commit, and the config centre publishes git
tags plus a per-resource content hash -- so there is no excuse for finding out
late.

Exit code is the interface: 0 = same build, 2 = drift. It is meant to gate a
lane run, not to be read by a person.

Deliberately compares EVERY axis it can rather than picking one. The bundle and
the config centre are versioned independently; a run that checked only the
bundle hash would sail straight past a changed device catalogue, which is the
half that actually carries the device knowledge.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import zipfile
from pathlib import Path

import requests

INDEX = "https://www.mchose.com.cn/"
GLOBAL_JSON = "https://cdn.mchose.com.cn/configCenter/config/global.json"
_SELF_VERSION = re.compile(r'console\.info\(\s*"[^"]*版本号[^"]*"\s*,\s*"([^"]+)"')


def _live() -> dict:
    s = requests.Session()
    html = s.get(INDEX, timeout=60).text
    m = re.search(r'src="(/assets/main-[^"]+\.js)"', html)
    out: dict = {"entry_chunk": m.group(1) if m else None}
    if m:
        js = s.get("https://www.mchose.com.cn" + m.group(1), timeout=60).text
        v = _SELF_VERSION.search(js)
        out["bundle_self_version"] = v.group(1) if v else None

    raw = json.loads(s.get(GLOBAL_JSON, timeout=60).content.decode("utf-8"))
    blob = base64.b64decode(raw["__compressBase64__"])
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        glob = json.loads(z.read(z.namelist()[0]).decode("utf-8"))
    out["web_version"] = glob.get("webVersion", {})
    out["config_versions"] = glob.get("version", {})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle-manifest", required=True)
    ap.add_argument("--configcenter-manifest", required=True)
    args = ap.parse_args()

    bm = json.loads(Path(args.bundle_manifest).read_text(encoding="utf-8"))
    cm = json.loads(Path(args.configcenter_manifest).read_text(encoding="utf-8"))
    live = _live()

    drift: list[str] = []

    was = (bm.get("self_reported_build") or {}).get("version")
    now = live.get("bundle_self_version")
    if was != now:
        drift.append(f"bundle self-reported version: recorded {was!r}, live {now!r}")

    was_hash = (cm.get("build_identities", {}).get("webVersion") or {}).get("hash")
    now_hash = (live.get("web_version") or {}).get("hash")
    if was_hash != now_hash:
        drift.append(f"webVersion.hash: recorded {was_hash!r}, live {now_hash!r}")

    recorded_cfg = cm.get("config_center_version_map", {})
    live_cfg = live.get("config_versions", {})
    for name in sorted(set(recorded_cfg) | set(live_cfg)):
        a, b = recorded_cfg.get(name), live_cfg.get(name)
        if a != b:
            drift.append(f"config `{name}`: recorded {a!r}, live {b!r}")

    if drift:
        print("MCHOSE HAS REDEPLOYED SINCE THIS CORPUS WAS TAKEN:")
        for d in drift:
            print(f"  - {d}")
        print("\nFindings written against the recorded build are about a build that is")
        print("no longer served. Re-acquire before treating them as current.")
        return 2

    print("no drift: bundle, webVersion and every config-centre resource match the manifests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
