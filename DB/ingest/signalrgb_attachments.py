"""Public SignalRGB USBData attachment ingestion into the existing CAS.

Downloads are inert byte streams: executables and archives are never launched.
The output directory contains hard links into the content-addressed store so
capture parsing does not duplicate large blobs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import requests


UPLOAD_RE = re.compile(r"\[[^]]*\]\((?P<markdown>/uploads/[^)]+|https?://[^)]+/uploads/[^)]+)\)")


def _public_url(issue: dict, attachment: str) -> str:
    if attachment.startswith("http"):
        return attachment
    # GitLab stores uploads outside the project namespace.  Markdown in issue
    # descriptions only contains ``/uploads/<secret>/<filename>``; resolving
    # it relative to the issue URL produces a non-existent
    # ``<namespace>/<project>/-/uploads/...`` path.  The API issue payload
    # carries the immutable numeric project ID required by the public upload
    # route instead.
    web_url = urlsplit(issue.get("web_url", ""))
    origin = f"{web_url.scheme}://{web_url.netloc}" if web_url.scheme and web_url.netloc else "https://gitlab.com"
    project_id = issue.get("project_id")
    if project_id:
        return f"{origin}/-/project/{project_id}/{attachment.lstrip('/')}"
    # Retain a deterministic fallback for legacy issue exports that omit the
    # project ID. It is useful metadata but is not assumed downloadable.
    return f"{origin}/{attachment.lstrip('/')}"


def _safe_name(value: str) -> str:
    name = PurePosixPath(urlsplit(value).path).name or "attachment.bin"
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name)[:180]


def discover_attachments(issues: list[dict]) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for issue in issues:
        for match in UPLOAD_RE.finditer(issue.get("description") or ""):
            raw = match.group("markdown").strip()
            url = _public_url(issue, raw)
            if url in seen:
                continue
            seen.add(url)
            found.append({
                "issue_iid": issue.get("iid"), "issue_id": issue.get("id"),
                "issue_url": issue.get("web_url"), "created_at": issue.get("created_at"),
                "attachment_url": url, "filename": _safe_name(url),
            })
    return found


def ingest_signalrgb_attachments(db_path: Path, issues_path: Path, output_root: Path,
                                 cas_root: Path, timeout: int = 90) -> dict[str, int]:
    issues = json.loads(issues_path.read_text(encoding="utf-8"))
    attachments = discover_attachments(issues)
    output_root.mkdir(parents=True, exist_ok=True)
    cas_root.mkdir(parents=True, exist_ok=True)
    stats: Counter[str] = Counter(issues=len(issues), issues_with_attachments=len({x["issue_iid"] for x in attachments}), attachments_found=len(attachments))
    manifest: list[dict] = []
    session = requests.Session()
    session.headers["User-Agent"] = "Vetro-Registry-Forensic-Ingest/1.0"
    conn = sqlite3.connect(db_path)
    auth_blocked = False
    try:
        for index, item in enumerate(attachments, 1):
            entry = dict(item)
            suffix = Path(item["filename"]).suffix.lower()
            entry["kind"] = "capture" if suffix in {".pcap", ".pcapng"} else ("text_log" if suffix in {".txt", ".log", ".xml", ".json", ".csv"} else "other")
            if auth_blocked:
                entry.update(status="access_blocked_auth", error="GitLab upload endpoint requires authenticated session")
                stats["attachments_access_blocked_auth"] += 1
                manifest.append(entry)
                continue
            try:
                with session.get(item["attachment_url"], stream=True, timeout=timeout) as response:
                    if response.status_code in {401, 403} or "/users/sign_in" in response.url:
                        auth_blocked = True
                        entry.update(status="access_blocked_auth", error=f"GitLab authentication/anti-bot gate: HTTP {response.status_code}")
                        stats["attachments_access_blocked_auth"] += 1
                        manifest.append(entry)
                        continue
                    response.raise_for_status()
                    with tempfile.NamedTemporaryFile(delete=False, dir=output_root, suffix=".part") as temporary:
                        temp_path = Path(temporary.name)
                        digest = hashlib.sha256()
                        size = 0
                        for block in response.iter_content(1024 * 1024):
                            if not block:
                                continue
                            temporary.write(block); digest.update(block); size += len(block)
                sha = digest.hexdigest()
                cas_path = cas_root / sha[:2] / sha
                cas_path.parent.mkdir(parents=True, exist_ok=True)
                if cas_path.exists():
                    temp_path.unlink()
                else:
                    shutil.move(str(temp_path), str(cas_path))
                link_name = f"issue-{item['issue_iid']}-{sha[:12]}-{item['filename']}"
                link_path = output_root / link_name
                if not link_path.exists():
                    try:
                        os.link(cas_path, link_path)
                    except OSError:
                        shutil.copy2(cas_path, link_path)
                entry.update(status="downloaded", sha256=sha, size=size,
                             content_type=response.headers.get("Content-Type"), local_name=link_name)
                conn.execute("""INSERT OR IGNORE INTO artifacts
                    (sha256,filename,size,original_url,final_url,content_type,etag,last_modified,normalized_url,extraction_status)
                    VALUES(?,?,?,?,?,?,?,?,?,'attachment_downloaded')""",
                    (sha, item["filename"], size, item["attachment_url"], response.url,
                     response.headers.get("Content-Type"), response.headers.get("ETag"),
                     response.headers.get("Last-Modified"), item["attachment_url"]))
                stats["attachments_downloaded"] += 1
                stats[entry["kind"]] += 1
            except Exception as exc:
                entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                stats["attachments_failed"] += 1
            manifest.append(entry)
            if index % 20 == 0:
                print(f"attachments {index}/{len(attachments)} downloaded={stats['attachments_downloaded']} failed={stats['attachments_failed']}", flush=True)
        for entry in manifest:
            external_id = hashlib.sha256(entry["attachment_url"].encode()).hexdigest()
            conn.execute("""INSERT OR REPLACE INTO external_attachments(
                external_id,source_name,issue_iid,issue_url,attachment_url,filename,kind,status,
                content_sha256,size,content_type,error,source_created_at,last_checked)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (external_id, "SignalRGB USBData", entry.get("issue_iid"), entry.get("issue_url"),
                 entry["attachment_url"], entry["filename"], entry["kind"], entry["status"],
                 entry.get("sha256"), entry.get("size"), entry.get("content_type"),
                 entry.get("error"), entry.get("created_at")))
        # Versions before the numeric-project URL fix recorded a false
        # ``access_blocked_auth`` result for the impossible project-relative
        # upload path.  Remove only those obsolete rows after their corrected
        # counterparts have been recorded; no downloaded evidence is removed.
        conn.execute("""DELETE FROM external_attachments
                        WHERE source_name='SignalRGB USBData'
                          AND status='access_blocked_auth'
                          AND attachment_url LIKE
                              'https://gitlab.com/signalrgb/signal-plugins/-/uploads/%'""")
        conn.commit()
    finally:
        conn.close()
    (output_root.parent / "manifest.json").write_text(json.dumps({"stats": dict(stats), "attachments": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(stats)
