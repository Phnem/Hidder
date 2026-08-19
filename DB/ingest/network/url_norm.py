"""URL normalization utilities for deduplication and caching."""

import urllib.parse
from typing import Set

TRACKING_PARAMS: Set[str] = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "gclsrc", "dclid", "_ga", "_gl", "msclkid", "spm",
    "_hsenc", "_hsmi", "mc_eid", "yclid", "zanpid"
}


def normalize_artifact_url(url: str) -> str:
    """
    Normalize an artifact URL to ensure equivalent URLs collapse to a single key:
    - Trim whitespace
    - Lowercase scheme and netloc
    - Strip standard default ports (:80, :443)
    - Remove known ad/analytics tracking query parameters (utm_*, fbclid, etc.)
    - Retain and sort versioning, token, ref, and functional parameters
    - Remove URL fragment (#...)
    - Normalize duplicate path slashes
    """
    if not url:
        return ""

    url_str = url.strip()
    try:
        parsed = urllib.parse.urlparse(url_str)
    except Exception:
        return url_str

    scheme = parsed.scheme.lower() if parsed.scheme else "https"
    netloc = parsed.netloc.lower()

    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    path = parsed.path
    while "//" in path:
        path = path.replace("//", "/")
    if not path:
        path = "/"

    query_parts = []
    if parsed.query:
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [
            (k, v) for (k, v) in params
            if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)
        ]
        filtered.sort(key=lambda item: (item[0], item[1]))
        if filtered:
            query_parts = [urllib.parse.urlencode(filtered)]

    query_str = query_parts[0] if query_parts else ""

    normalized = urllib.parse.urlunparse((
        scheme, netloc, path, "", query_str, ""
    ))
    return normalized
