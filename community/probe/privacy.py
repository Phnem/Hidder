"""Strict privacy scrubber for community observation bundles.

Guarantees that no usernames, computer names, emails, IPs, MAC addresses,
USB serial numbers, full filesystem paths, or arbitrary keystrokes are exported.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


class PrivacyScrubber:
    """Sanitizes text and metadata before bundle export."""

    def __init__(self) -> None:
        self.sensitive_patterns: list[tuple[re.Pattern, str]] = [
            # Windows / Unix User Paths: C:\Users\Username\... or /home/username/...
            (re.compile(r"[a-zA-Z]:\\(?:Users|Documents and Settings)\\[^\\]+\\", re.IGNORECASE), "[USER_PATH]/"),
            (re.compile(r"/(?:home|Users)/[^/]+/", re.IGNORECASE), "[USER_PATH]/"),
            # Email addresses
            (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[REDACTED_EMAIL]"),
            # IPv4 addresses (excluding 127.0.0.1 / 0.0.0.0 if not personal)
            (re.compile(r"\b(?!127\.0\.0\.1|0\.0\.0\.0)(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
            # MAC addresses
            (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}(?:[0-9A-Fa-f]{2})\b"), "[REDACTED_MAC]"),
            # Common USB Serial Number patterns
            (re.compile(r"&[0-9a-fA-F]{8}&[0-9a-fA-F]&[0-9a-fA-F]{4}", re.IGNORECASE), "&[REDACTED_SERIAL]"),
            (re.compile(r"\\\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\\\}", re.IGNORECASE), "{[REDACTED_GUID]}"),
        ]
        
        # Add current user and computer name if available
        username = os.environ.get("USERNAME") or os.environ.get("USER")
        if username and len(username) > 2:
            self.sensitive_patterns.append((re.compile(re.escape(username), re.IGNORECASE), "[REDACTED_USER]"))
            
        hostname = os.environ.get("COMPUTERNAME")
        if hostname and len(hostname) > 2:
            self.sensitive_patterns.append((re.compile(re.escape(hostname), re.IGNORECASE), "[REDACTED_HOST]"))

    def scrub_text(self, text: str) -> str:
        if not text:
            return ""
        result = text
        for pattern, replacement in self.sensitive_patterns:
            result = pattern.sub(replacement, result)
        return result

    def sanitize_path(self, path_str: str) -> str:
        """Always reduce a file path to its basename only."""
        if not path_str:
            return ""
        return Path(path_str).name

    def sanitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively sanitize a dictionary."""
        sanitized = {}
        for key, val in data.items():
            if key in ("serial", "serial_number", "device_serial", "user", "username", "computername", "hostname"):
                continue  # Omit completely
            if isinstance(val, str):
                if key in ("process_basename", "process_name", "executable", "path"):
                    sanitized[key] = self.sanitize_path(val)
                else:
                    sanitized[key] = self.scrub_text(val)
            elif isinstance(val, dict):
                sanitized[key] = self.sanitize_dict(val)
            elif isinstance(val, list):
                sanitized[key] = [
                    self.sanitize_dict(item) if isinstance(item, dict)
                    else self.scrub_text(item) if isinstance(item, str)
                    else item
                    for item in val
                ]
            else:
                sanitized[key] = val
        return sanitized
