"""Resilient Network Layer with Cloudflare/SPA Tiered Fetcher."""

import asyncio
import hashlib
import re
import time
from pathlib import Path
from typing import Optional, NamedTuple
import urllib.parse

from ingest.config import settings
from ingest.logging_setup import log_http, log_net, get_logger

logger = get_logger()

# Optional curl_cffi import
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    import urllib.request
    HAS_CURL_CFFI = False

# Optional Playwright import
try:
    from playwright.async_api import async_playwright
    from playwright_stealth import stealth_async
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class FetchResponse(NamedTuple):
    status_code: int
    text: str
    content: bytes
    headers: dict[str, str]
    url: str
    is_cached: bool = False
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    elapsed_ms: int = 0


class TieredFetcher:
    def __init__(self):
        self.session = None
        self._init_session()
        self.host_last_request_time: dict[str, float] = {}

    def _init_session(self):
        if HAS_CURL_CFFI:
            self.session = curl_requests.Session(impersonate=settings.impersonate_profile)
        else:
            self.session = None

    def _apply_rate_limit(self, url: str):
        """Ensure minimum delay between requests to the same host."""
        host = urllib.parse.urlparse(url).netloc
        now = time.time()
        last_time = self.host_last_request_time.get(host, 0.0)
        diff = now - last_time
        if diff < settings.request_delay_seconds:
            time.sleep(settings.request_delay_seconds - diff)
        self.host_last_request_time[host] = time.time()

    def get(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        allow_fallback: bool = True
    ) -> FetchResponse:
        """
        Execute a tiered GET request:
        Tier 1: Fast curl_cffi with Chrome 120 TLS fingerprint
        Tier 2: Headless Playwright Stealth if WAF/Cloudflare JS challenge is encountered
        """
        self._apply_rate_limit(url)
        start_time = time.time()

        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        log_net(f"[Tier 1] GET {url} (curl_cffi impersonate={settings.impersonate_profile})")

        # Execute Tier 1
        try:
            if HAS_CURL_CFFI:
                resp = self.session.get(url, headers=headers, timeout=settings.request_timeout_seconds, allow_redirects=True)
                elapsed = int((time.time() - start_time) * 1000)
                status = resp.status_code
                content = resp.content
                text = resp.text
                resp_headers = dict(resp.headers)
            else:
                # Basic fallback
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=settings.request_timeout_seconds) as u_resp:
                    content = u_resp.read()
                    text = content.decode("utf-8", errors="replace")
                    status = u_resp.status
                    resp_headers = dict(u_resp.headers)
                elapsed = int((time.time() - start_time) * 1000)

            # Check 304 Not Modified
            if status == 304:
                log_http(f"GET {url} -> 304 Not Modified [CACHE HIT] ({elapsed} ms)")
                return FetchResponse(304, "", b"", resp_headers, url, is_cached=True, elapsed_ms=elapsed)

            log_http(f"GET {url} -> Status: {status} | Size: {len(content)} bytes | Elapsed: {elapsed} ms")

            # Check for Cloudflare / Turnstile challenge markers
            is_cf_challenge = (
                status in [403, 503] or
                "Just a moment..." in text or
                "cf-browser-verification" in text or
                "challenge-platform" in text or
                ("Server" in resp_headers and "cloudflare" in resp_headers["Server"].lower() and len(content) < 3000)
            )

            if is_cf_challenge and allow_fallback and HAS_PLAYWRIGHT:
                cf_ray = resp_headers.get("cf-ray", "N/A")
                log_net(f"Cloudflare challenge detected (HTTP {status} / CF-Ray: {cf_ray})")
                log_net("[Tier 2] Escalating to Playwright Stealth...")
                return self._solve_with_playwright(url)

            return FetchResponse(
                status_code=status,
                text=text,
                content=content,
                headers=resp_headers,
                url=url,
                etag=resp_headers.get("etag") or resp_headers.get("ETag"),
                last_modified=resp_headers.get("last-modified") or resp_headers.get("Last-Modified"),
                elapsed_ms=elapsed
            )

        except Exception as e:
            logger.warning(f"[net] Tier 1 fetch error for {url}: {e}")
            if allow_fallback and HAS_PLAYWRIGHT:
                log_net("[Tier 2] Escalating to Playwright Stealth due to network failure...")
                return self._solve_with_playwright(url)
            return FetchResponse(500, "", b"", {}, url, elapsed_ms=int((time.time() - start_time) * 1000))

    def _solve_with_playwright(self, url: str) -> FetchResponse:
        """Run headless Playwright browser to solve challenge and sync cookies."""
        try:
            return asyncio.run(self._async_playwright_solve(url))
        except Exception as e:
            logger.error(f"[net] Playwright solver exception: {e}", exc_info=True)
            return FetchResponse(500, "", b"", {}, url)

    async def _async_playwright_solve(self, url: str) -> FetchResponse:
        start_t = time.time()
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent=settings.user_agent,
                viewport={"width": 1920, "height": 1080},
                locale="en-US"
            )
            page = await context.new_page()
            await stealth_async(page)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                # Wait for title to clear "Just a moment..."
                for _ in range(25):
                    title = await page.title()
                    if "just a moment" not in title.lower():
                        break
                    await asyncio.sleep(0.5)

                # Wait slightly for dynamic content
                await asyncio.sleep(2.0)

                content_html = await page.content()
                cookies = await context.cookies()

                # Sync cookies to curl_cffi session
                cf_clearance_val = "N/A"
                if HAS_CURL_CFFI and self.session:
                    for c in cookies:
                        self.session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
                        if c["name"] == "cf_clearance":
                            cf_clearance_val = c["value"]

                duration = time.time() - start_t
                log_net(f"Browser challenge solved in {duration:.1f}s. Extracted cf_clearance: {cf_clearance_val[:12]}...")
                log_net("Session cookies updated, resuming fast crawl.")

                return FetchResponse(
                    status_code=200,
                    text=content_html,
                    content=content_html.encode("utf-8"),
                    headers={},
                    url=page.url,
                    elapsed_ms=int(duration * 1000)
                )
            finally:
                await browser.close()

    def check_artifact_conditional(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        timeout: float = 6.0
    ) -> tuple[int, dict[str, str]]:
        """
        Perform a fast conditional request with If-None-Match and If-Modified-Since.
        Returns: (http_status_code, response_headers_dict)
        e.g. status 304 if unchanged.
        """
        self._apply_rate_limit(url)
        headers = {"User-Agent": settings.user_agent}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        if HAS_CURL_CFFI and self.session:
            try:
                r = self.session.head(url, headers=headers, timeout=timeout, allow_redirects=True)
                resp_headers = {k.lower(): v for k, v in r.headers.items()}
                return r.status_code, resp_headers
            except Exception:
                try:
                    r = self.session.get(url, headers=headers, timeout=timeout, stream=True)
                    resp_headers = {k.lower(): v for k, v in r.headers.items()}
                    return r.status_code, resp_headers
                except Exception as e:
                    logger.debug(f"[fetcher] Conditional check failed for {url}: {e}")
                    return 0, {}
        else:
            try:
                req = urllib.request.Request(url, headers=headers, method="HEAD")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    return resp.status, resp_headers
            except urllib.error.HTTPError as e:
                return e.code, {k.lower(): v for k, v in e.headers.items()} if hasattr(e, "headers") else {}
            except Exception:
                return 0, {}

    def download_file_stream(
        self,
        url: str,
        dest_temp_path: Path,
        max_bytes: int = settings.max_artifact_size_bytes,
        progress_callback=None
    ) -> tuple[int, str, str, Optional[str], Optional[str], Optional[str]]:
        """
        Stream download large files with chunked SHA-256 calculation.
        Returns: (file_size_bytes, sha256_hex, final_url, content_type, etag, last_modified)
        """
        self._apply_rate_limit(url)
        log_http(f"Downloading stream: {url} -> {dest_temp_path.name}")

        hasher = hashlib.sha256()
        downloaded = 0
        dest_temp_path.parent.mkdir(parents=True, exist_ok=True)
        final_url = url
        content_type = None
        etag = None
        last_modified = None

        clean_url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
        req = urllib.request.Request(clean_url, headers={"User-Agent": settings.user_agent})
        with urllib.request.urlopen(req, timeout=35.0) as resp, open(dest_temp_path, "wb") as f:
            final_url = resp.geturl() if hasattr(resp, "geturl") else url
            content_type = resp.headers.get("Content-Type") if hasattr(resp, "headers") else None
            etag = resp.headers.get("ETag") if hasattr(resp, "headers") else None
            last_modified = resp.headers.get("Last-Modified") if hasattr(resp, "headers") else None
            total_len = int(resp.headers.get("Content-Length", 0) or 0)
            if total_len > max_bytes:
                raise ValueError(f"Content-Length {total_len} bytes exceeds max limit ({max_bytes} bytes)")
            while chunk := resp.read(1024 * 512):
                f.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise ValueError(f"Download exceeded limit ({max_bytes} bytes)")
                if progress_callback and total_len > 0:
                    progress_callback(downloaded, total_len)

        sha = hasher.hexdigest()
        log_http(f"Download complete: {dest_temp_path.name} ({downloaded / 1024 / 1024:.2f} MB, SHA: {sha[:12]}...)")
        return downloaded, sha, str(final_url), content_type, etag, last_modified
