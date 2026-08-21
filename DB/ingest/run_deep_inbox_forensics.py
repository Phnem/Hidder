from pathlib import Path
from ingest.deep_inbox_forensics import run_inbox_forensics

run_inbox_forensics(
    Path("protocol-miner/inbox"),
    Path("reports/inbox_deep_forensics_inventory.json"),
    Path("protocol-miner/forensics/extracted"),
)
