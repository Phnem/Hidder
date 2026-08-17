//! Linux escape hatch: `hidraw` specifics beyond what `hidapi` exposes.
//!
//! Empty. Note for whoever fills it: the user-visible failure on Linux is
//! almost always a missing udev rule, and it presents as "no device" with the
//! keyboard plugged in. That distinction belongs in
//! [`crate::TransportError::AccessDenied`], not in a log line.
