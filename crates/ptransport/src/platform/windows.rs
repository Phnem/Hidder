//! Windows escape hatch: direct `CreateFile` / `HidD_*` calls.
//!
//! Empty. TICKET-08 decides whether anything is needed here, by measuring what
//! `hidapi` can and cannot reach on real boards: which top-level collections
//! open, which return access-denied, and whether a vendor-defined collection
//! carrying the analog stream is reachable from user space at all (spec.md AC3,
//! open question Q2).
