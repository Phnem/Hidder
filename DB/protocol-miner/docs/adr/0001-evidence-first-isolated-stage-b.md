# ADR 0001: Evidence-first isolated Stage B

## Status

Accepted (2026-08-19).

## Context

The sibling Registry Ingest project discovers vendor artifacts and maintains a
staging registry. Protocol extraction needs deeper, iterative analysis without
turning that project into a reverse-engineering monolith or allowing inferred
packet layouts into production data.

## Decision

Protocol Miner lives in `protocol-miner/` as a standalone Python application.
It may reuse the compatible content-addressed byte store in `../artifacts`, but
owns its derived workspace, evidence, candidates, reports, and schemas.

Every extractor emits an observation with an artifact SHA-256, source location,
extractor version, confidence class, and raw value. Synthesis produces facts
and candidates only through references to those observations. A missing fact is
reported as unknown; inference is explicit and never silently replaces stronger
evidence. Registry output is a versioned staging patch, never a direct database
mutation.

## Consequences

- The Registry Ingest SQLite schema remains unchanged.
- CAS is reused by SHA-256 so inputs are not duplicated.
- Static analysis is the default. Dynamic adapters, if added, require an
  explicit flag and an environment where real HID is unavailable.
- The codebase has no `send_raw_hid` or equivalent transport API.
