//! Rate limiting, per family and per device.

use std::collections::HashMap;

use ptransport::DeviceId;

use crate::class::{Cadence, OpcodeClass};
use crate::command::cadence_for;

/// Proof that a human was asked about this one operation and said yes.
///
/// Taken by value everywhere it is used, so it cannot be held and reused. That
/// is the whole point: an unknown family gets one operation at a time, and
/// "one at a time" is not a policy anyone has to remember to follow if a second
/// operation simply has nothing to present.
#[derive(Debug)]
pub struct UserConfirmation {
    _private: (),
}

impl UserConfirmation {
    /// Called by the layer that actually put the question in front of a person.
    pub fn given() -> Self {
        Self { _private: () }
    }
}

/// Why an operation has to wait.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum WaitReason {
    /// The family's measured minimum interval between operations.
    MinGapBefore,
    /// The quiet period the previous operation requires after itself.
    SettleAfterPrevious,
    /// The family's measured burst allowance is spent for now.
    BurstWindow,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum RateDecision {
    Allow,
    Wait {
        ms: u64,
        reason: WaitReason,
    },
    /// The family has no measured timing, so there is nothing to throttle
    /// against. No interval is invented; a person is asked instead.
    NeedsConfirmation,
}

#[derive(Debug, Default)]
struct DeviceRate {
    /// When the last operation finished, and the quiet it asked for.
    last_finished_ms: Option<u64>,
    settle_until_ms: u64,
    next_allowed_ms: u64,
    /// Completion times within the current burst window, oldest first.
    recent: Vec<u64>,
}

/// Enforces the measured cadence of each family against each device.
///
/// Holds no handle and performs no I/O: it answers "may this go now", and the
/// gate is what acts on the answer.
#[derive(Debug, Default)]
pub struct RateLimiter {
    devices: HashMap<DeviceId, DeviceRate>,
}

impl RateLimiter {
    pub fn new() -> Self {
        Self::default()
    }

    /// Whether this operation resolves to a measured cadence at all.
    ///
    /// Takes the command as well as the class, because a command may carry its
    /// own measurement while its class carries none -- which is the normal state
    /// for a family whose first command has just been verified.
    pub fn is_measured(family: &str, command: Option<&str>, class: OpcodeClass) -> bool {
        cadence_for(family, command, class).is_some()
    }

    /// The cadence that governs this operation, and where it came from.
    pub fn cadence(family: &str, command: Option<&str>, class: OpcodeClass) -> Option<Cadence> {
        cadence_for(family, command, class)
    }

    /// May this operation go to this device now?
    ///
    /// `confirmation` is consumed rather than borrowed, so a caller cannot hold
    /// one and drive a series with it.
    ///
    /// `command` participates because timing resolves per command first and per
    /// class second: a command measured on hardware here must not lend its
    /// numbers to the next command in the same class, and must not be held back
    /// by a class number measured for something else.
    pub fn check(
        &self,
        device: DeviceId,
        family: &str,
        command: Option<&str>,
        class: OpcodeClass,
        now_ms: u64,
        confirmation: Option<UserConfirmation>,
    ) -> RateDecision {
        let Some(timing) = cadence_for(family, command, class) else {
            // Unknown family, or a known family with no measurement for this
            // class. There is deliberately no fallback interval here: a made-up
            // "safe 12 ms" is a guess wearing the costume of a measurement.
            return match confirmation {
                Some(_) => RateDecision::Allow,
                None => RateDecision::NeedsConfirmation,
            };
        };

        let state = match self.devices.get(&device) {
            Some(state) => state,
            None => return RateDecision::Allow,
        };

        if now_ms < state.settle_until_ms {
            return RateDecision::Wait {
                ms: state.settle_until_ms - now_ms,
                reason: WaitReason::SettleAfterPrevious,
            };
        }
        if now_ms < state.next_allowed_ms {
            return RateDecision::Wait {
                ms: state.next_allowed_ms - now_ms,
                reason: WaitReason::MinGapBefore,
            };
        }
        if let Some(burst) = timing.burst {
            // `saturating_sub` can only widen the window, never narrow it, so
            // early in a process's life the count is conservative rather than
            // permissive. That is the direction to err in.
            let window_start = now_ms.saturating_sub(burst.per_window_ms);
            let in_window = state
                .recent
                .iter()
                .filter(|finished| **finished >= window_start)
                .count();
            if in_window >= burst.max_operations as usize {
                let oldest = state
                    .recent
                    .iter()
                    .filter(|finished| **finished >= window_start)
                    .min()
                    .copied()
                    .unwrap_or(now_ms);
                return RateDecision::Wait {
                    ms: (oldest + burst.per_window_ms).saturating_sub(now_ms),
                    reason: WaitReason::BurstWindow,
                };
            }
        }
        RateDecision::Allow
    }

    /// Records that an operation finished, which is what starts its settle
    /// period. Called whether the operation succeeded or failed: a write that
    /// errored still touched the device, and the quiet it owes is not refunded.
    pub fn record(
        &mut self,
        device: DeviceId,
        family: &str,
        command: Option<&str>,
        class: OpcodeClass,
        finished_ms: u64,
    ) {
        let state = self.devices.entry(device).or_default();
        state.last_finished_ms = Some(finished_ms);
        state.recent.push(finished_ms);
        if let Some(timing) = cadence_for(family, command, class) {
            state.next_allowed_ms = finished_ms + timing.min_gap_before_ms;
            state.settle_until_ms = finished_ms + timing.settle_after_ms;
            if let Some(burst) = timing.burst {
                let window_start = finished_ms.saturating_sub(burst.per_window_ms);
                state.recent.retain(|finished| *finished >= window_start);
            } else {
                state.recent = vec![finished_ms];
            }
        } else {
            // Unknown family: no measured interval, and none invented. The next
            // operation needs its own confirmation regardless of the clock.
            state.next_allowed_ms = finished_ms;
            state.settle_until_ms = finished_ms;
            state.recent = vec![finished_ms];
        }
    }

    /// Forgets a device's cadence, on disconnect.
    pub fn forget(&mut self, device: DeviceId) {
        self.devices.remove(&device);
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    const DEVICE: fn() -> DeviceId = || DeviceId::new(1);
    const OTHER_DEVICE: fn() -> DeviceId = || DeviceId::new(2);
    const KNOWN: &str = "royuan-gen2";
    const UNKNOWN: &str = "nobody-has-seen-this";

    // --- a measured family is throttled by its own numbers ---------------

    #[test]
    fn the_first_operation_goes_immediately() {
        let limiter = RateLimiter::new();
        assert_eq!(
            limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 0, None),
            RateDecision::Allow
        );
    }

    #[test]
    fn a_second_operation_waits_the_families_measured_gap() {
        let mut limiter = RateLimiter::new();
        limiter.record(DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 1_000);

        // royuan-gen2 declares 12 ms between reads, measured on hardware.
        assert_eq!(
            limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 1_005, None),
            RateDecision::Wait {
                ms: 7,
                reason: WaitReason::MinGapBefore
            }
        );
        assert_eq!(
            limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 1_012, None),
            RateDecision::Allow
        );
    }

    #[test]
    fn the_quiet_after_a_flash_write_applies_to_everything_that_follows() {
        // The lesson from prior art: the settle period belongs to the write
        // that just happened, not to the next write of the same kind. A read
        // issued into that window is still traffic on a busy endpoint.
        let mut limiter = RateLimiter::new();
        limiter.record(DEVICE(), KNOWN, None, OpcodeClass::SlowFlash, 0);

        match limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 500, None) {
            RateDecision::Wait { ms, reason } => {
                assert_eq!(reason, WaitReason::SettleAfterPrevious);
                assert_eq!(ms, 1_500, "royuan-gen2 declares a 2 s settle");
            }
            other => panic!("a read during the settle window was allowed: {other:?}"),
        }
        assert_eq!(
            limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 2_000, None),
            RateDecision::Allow
        );
    }

    #[test]
    fn the_burst_allowance_is_the_measured_one() {
        // royuan-gen2: at most 2 flash uploads per 10 s.
        let mut limiter = RateLimiter::new();
        limiter.record(DEVICE(), KNOWN, None, OpcodeClass::SlowFlash, 0);
        limiter.record(DEVICE(), KNOWN, None, OpcodeClass::SlowFlash, 3_000);

        match limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SlowFlash, 6_000, None) {
            RateDecision::Wait { reason, .. } => assert_eq!(reason, WaitReason::BurstWindow),
            other => panic!("a third upload inside the window was allowed: {other:?}"),
        }
        assert_eq!(
            limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SlowFlash, 10_001, None),
            RateDecision::Allow,
            "once the window has passed the allowance returns"
        );
    }

    #[test]
    fn a_failed_operation_still_owes_its_quiet_period() {
        let mut limiter = RateLimiter::new();
        // record() is what the gate calls on completion, success or not.
        limiter.record(DEVICE(), KNOWN, None, OpcodeClass::SlowFlash, 0);
        assert!(matches!(
            limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 100, None),
            RateDecision::Wait { .. }
        ));
    }

    // --- unknown families get no invented numbers ------------------------

    #[test]
    fn an_unknown_family_is_never_given_a_default_interval() {
        let limiter = RateLimiter::new();
        assert_eq!(
            limiter.check(DEVICE(), UNKNOWN, None, OpcodeClass::SafeWrite, 0, None),
            RateDecision::NeedsConfirmation,
            "an unmeasured family must ask, not guess"
        );
        assert!(
            !RateLimiter::is_measured(UNKNOWN, None, OpcodeClass::SafeWrite),
            "nothing should claim to have measured this family"
        );
    }

    #[test]
    fn an_unknown_family_needs_a_fresh_confirmation_for_every_operation() {
        let mut limiter = RateLimiter::new();
        assert_eq!(
            limiter.check(
                DEVICE(),
                UNKNOWN,
                None,
                OpcodeClass::SafeWrite,
                0,
                Some(UserConfirmation::given())
            ),
            RateDecision::Allow
        );
        limiter.record(DEVICE(), UNKNOWN, None, OpcodeClass::SafeWrite, 10);

        // No amount of elapsed time turns the next one into an automatic
        // operation. The confirmation was spent on the first.
        assert_eq!(
            limiter.check(
                DEVICE(),
                UNKNOWN,
                None,
                OpcodeClass::SafeWrite,
                10_000_000,
                None
            ),
            RateDecision::NeedsConfirmation,
            "consent to one operation became consent to a series"
        );
    }

    #[test]
    fn a_known_family_with_no_measurement_for_a_class_is_treated_as_unknown() {
        // royuan-gen2 has measured timing for reads and flash writes, but has
        // never had a safe_write measured. The gap must not borrow the read's
        // number just because it is nearby.
        assert!(!RateLimiter::is_measured(
            KNOWN,
            None,
            OpcodeClass::SafeWrite
        ));
        let limiter = RateLimiter::new();
        assert_eq!(
            limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SafeWrite, 0, None),
            RateDecision::NeedsConfirmation
        );
    }

    // --- limits belong to a family and a device, not to the process -------

    #[test]
    fn two_devices_do_not_share_a_cooldown() {
        let mut limiter = RateLimiter::new();
        limiter.record(DEVICE(), KNOWN, None, OpcodeClass::SlowFlash, 0);
        assert_eq!(
            limiter.check(OTHER_DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 1, None),
            RateDecision::Allow,
            "one keyboard's settle period stalled another keyboard"
        );
    }

    #[test]
    fn one_familys_numbers_are_not_applied_to_another() {
        // yc500 and gen2 both declare 12 ms today, so compare the declarations
        // rather than the behaviour: the point is that each is looked up, not
        // that they currently differ.
        let gen2 =
            crate::command::timing_for("royuan-gen2", OpcodeClass::SafeRead).expect("measured");
        let yc500 =
            crate::command::timing_for("royuan-yc500", OpcodeClass::SafeRead).expect("measured");
        assert_eq!(gen2.family, "royuan-gen2");
        assert_eq!(yc500.family, "royuan-yc500");
        assert!(
            crate::command::timing_for("aula-hero84-he", OpcodeClass::SafeRead).is_none(),
            "the board we own has no measured timing and must not inherit one"
        );
    }

    #[test]
    fn a_command_with_no_measurement_of_its_own_falls_back_to_its_class() {
        let cadence = RateLimiter::cadence(KNOWN, Some("identify"), OpcodeClass::ProbeOk)
            .expect("gen2 measures its probe class");
        assert_eq!(cadence.source, crate::class::CadenceSource::Class);
    }

    #[test]
    fn an_unmeasured_family_stays_unmeasured_whatever_the_command_is_called() {
        // The fallback chain ends at nothing, not at a default. A name the ACL
        // has never heard of must not conjure a cadence.
        assert!(!RateLimiter::is_measured(
            UNKNOWN,
            Some("read_model_id"),
            OpcodeClass::SafeRead
        ));
        assert!(!RateLimiter::is_measured(
            UNKNOWN,
            None,
            OpcodeClass::SafeRead
        ));
    }

    #[test]
    fn forgetting_a_device_clears_its_cadence() {
        let mut limiter = RateLimiter::new();
        limiter.record(DEVICE(), KNOWN, None, OpcodeClass::SlowFlash, 0);
        limiter.forget(DEVICE());
        assert_eq!(
            limiter.check(DEVICE(), KNOWN, None, OpcodeClass::SafeRead, 1, None),
            RateDecision::Allow
        );
    }
}
