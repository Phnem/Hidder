"""Safe non-mutating open-identity-close diagnostic reproducer for HERO84.

Performs strictly:
open -> GET identity -> close -> open -> GET identity -> close
ZERO mutating SET operations.
"""

from __future__ import annotations

import time
import sys
from .aula_transport import AulaHidTransport
from .identity import discover_real_instance_via_raw


def run_open_identity_close_diagnostic(uuid: int = 18691697672197) -> bool:
    print(f"=== HERO84 SAFE REPEATED HANDLE ACQUISITION DIAGNOSTIC ===", file=sys.stderr)
    print(f"[*] Target UUID: {uuid:#x}", file=sys.stderr)

    # Pass 1: First Acquisition
    print("[1] PASS 1: Opening real transport...", file=sys.stderr)
    t0 = time.time()
    t1 = AulaHidTransport.open_real(uuid=uuid)
    dur1 = (time.time() - t0) * 1000
    print(f"[1] PASS 1: Opened in {dur1:.1f}ms", file=sys.stderr)

    inst1 = discover_real_instance_via_raw(t1.raw)
    print(f"[1] PASS 1: Identity GET: {inst1.product_string} FW {inst1.firmware_version} VID={inst1.vid} PID={inst1.pid}", file=sys.stderr)

    print("[1] PASS 1: Closing transport...", file=sys.stderr)
    t1.close()
    print("[1] PASS 1: Transport closed.", file=sys.stderr)

    time.sleep(0.1)

    # Pass 2: Second Acquisition (proves handle release & reacquisition)
    print("[2] PASS 2: Opening real transport second time...", file=sys.stderr)
    t0 = time.time()
    t2 = AulaHidTransport.open_real(uuid=uuid)
    dur2 = (time.time() - t0) * 1000
    print(f"[2] PASS 2: Opened in {dur2:.1f}ms", file=sys.stderr)

    inst2 = discover_real_instance_via_raw(t2.raw)
    print(f"[2] PASS 2: Identity GET: {inst2.product_string} FW {inst2.firmware_version} VID={inst2.vid} PID={inst2.pid}", file=sys.stderr)

    print("[2] PASS 2: Closing transport...", file=sys.stderr)
    t2.close()
    print("[2] PASS 2: Transport closed.", file=sys.stderr)

    print("=== HERO84 REPEATED HANDLE ACQUISITION: 100% PASS ===", file=sys.stderr)
    return True


if __name__ == "__main__":
    ok = run_open_identity_close_diagnostic()
    sys.exit(0 if ok else 1)
