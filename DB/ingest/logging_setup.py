"""Logging infrastructure for Peripheral Registry Ingest."""

import datetime
import logging
import sys
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

from ingest.config import LOGS_DIR

custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "critical": "bold white on red",
    "debug": "dim white",
    "http": "bold green",
    "discovery": "bold magenta",
    "artifact": "bold blue",
    "extract": "bold yellow",
    "scan": "bold cyan",
    "fact": "bold green",
    "hint": "bold magenta",
    "dedupe": "cyan",
    "db": "bright_blue",
    "change": "bold yellow",
    "net": "bold magenta",
})

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(theme=custom_theme, color_system="auto", width=160, legacy_windows=False)

_current_log_file: Optional[Path] = None


class PreciseFormatter(logging.Formatter):
    """Custom formatter providing millisecond precision and clean output."""
    def formatTime(self, record, datefmt=None):
        ct = datetime.datetime.fromtimestamp(record.created)
        return ct.strftime("%H:%M:%S.%f")[:-3]


class LogMetricsTracker(logging.Handler):
    """Handler that intercepts and classifies all emitted log records into exact metrics."""
    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        self.counts = {
            "fatal_errors": 0,
            "collector_errors": 0,
            "artifact_download_failures": 0,
            "parse_failures": 0,
            "warnings": 0,
        }

    def emit(self, record: logging.LogRecord):
        try:
            msg = str(record.getMessage())
        except Exception:
            msg = str(record.msg)

        if record.levelno == logging.WARNING:
            self.counts["warnings"] += 1
        elif record.levelno >= logging.ERROR:
            msg_lower = msg.lower()
            if "[artifact]" in msg or "failed to download artifact" in msg_lower or "download error" in msg_lower or "404" in msg or "timed out" in msg_lower:
                self.counts["artifact_download_failures"] += 1
            elif "[scan]" in msg or "[extract]" in msg or "scanner error" in msg_lower or "extraction error" in msg_lower or "parse" in msg_lower or "corrupt" in msg_lower:
                self.counts["parse_failures"] += 1
            elif "critical error running brand collector" in msg_lower or "fatal" in msg_lower or "pipeline abort" in msg_lower:
                self.counts["fatal_errors"] += 1
            elif "error processing product" in msg_lower or "collector" in msg_lower or "adapter" in msg_lower:
                self.counts["collector_errors"] += 1
            else:
                self.counts["collector_errors"] += 1


_metrics_tracker = LogMetricsTracker()


def get_log_metrics() -> dict[str, int]:
    """Retrieve the classified counts of emitted log records."""
    return dict(_metrics_tracker.counts)


def reset_log_metrics():
    """Reset the classified counts of emitted log records."""
    _metrics_tracker.reset()


def setup_logging(verbose: bool = True, log_to_file: bool = True) -> tuple[logging.Logger, Path]:
    """Configure console and file loggers with millisecond timestamps and rich markup."""
    global _current_log_file

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = LOGS_DIR / f"run-{timestamp}.log"
    _current_log_file = log_file

    reset_log_metrics()

    root_logger = logging.getLogger("ingest")
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Clear previous handlers
    root_logger.handlers.clear()

    # 1. Metrics Tracker Handler (intercepts all records by class)
    root_logger.addHandler(_metrics_tracker)

    # 2. Console Handler using Rich
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        level=logging.DEBUG if verbose else logging.INFO,
        markup=True,
    )
    rich_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    root_logger.addHandler(rich_handler)

    # 3. File Handler (captures all logs with timestamp & level)
    if log_to_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = PreciseFormatter(
            fmt="[%(asctime)s] [%(levelname)-7s] %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    return root_logger, log_file


def get_logger() -> logging.Logger:
    """Retrieve the primary logger."""
    return logging.getLogger("ingest")


# Convenience prefix logging helpers
def log_http(msg: str):
    get_logger().info(f"[http][HTTP][/http] {msg}")


def log_net(msg: str):
    get_logger().info(f"[net][NET][/net] {msg}")


def log_discovery(vendor: str, model: str, category: str, url: str):
    get_logger().info(f"[discovery][DISCOVERY][/discovery] Vendor: [bold]{vendor}[/bold] | Model: [bold]{model}[/bold] | Category: {category} | URL: {url}")


def log_artifact(msg: str):
    get_logger().info(f"[artifact][ARTIFACT][/artifact] {msg}")


def log_extract(msg: str):
    get_logger().info(f"[extract][EXTRACT][/extract] {msg}")


def log_scan(msg: str):
    get_logger().info(f"[scan][SCAN][/scan] {msg}")


def log_fact(product: str, key: str, value: str, source: str, evidence: str = "device_identity"):
    get_logger().info(f"[fact][FACT][/fact] [bold]{product}[/bold] | {key} = [bold]{value}[/bold] (Source: {source}, Evidence: {evidence})")


def log_hint(product: str, key: str, value: str, source: str):
    get_logger().info(f"[hint][HINT][/hint] [bold]{product}[/bold] | {key} = [bold]{value}[/bold] (Source: {source})")


def log_dedupe(msg: str):
    get_logger().info(f"[dedupe][DEDUPE][/dedupe] {msg}")


def log_db(msg: str):
    get_logger().info(f"[db][DB][/db] {msg}")


def log_change(msg: str):
    get_logger().info(f"[change][CHANGE][/change] {msg}")
