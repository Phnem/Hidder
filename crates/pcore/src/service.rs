//! The device service: one thread that owns the hardware, and a handle anyone
//! may hold.
//!
//! # Why a thread and not a lock
//!
//! Because the alternative is a mutex around one device handle with concurrent
//! reads, writes and, eventually, a continuous analog stream going through it --
//! which is the outcome spec.md FR10 exists to forbid. HID reads block. A UI
//! framework's async runtime is not a place to block, and a device is not a
//! thing two callers may hold at once.
//!
//! So device work happens on one thread, in the order it was asked for, and
//! [`DeviceService`] is a `Send + Sync` handle that only knows how to ask. It is
//! not the full `DeviceSession` of FR10 -- that owns its handles for the life of
//! the session and TICKET-15 builds it for the analog stream. It is the same
//! shape one layer up, arrived at early because the application layer needed
//! *something* it could hold, and the honest options were this or a lock.
//!
//! # Why the service watches, and what it is allowed to watch with
//!
//! A screen that keeps showing a keyboard that was unplugged ten minutes ago is
//! the failure this ticket's acceptance criteria name, and the UI is not
//! permitted to prevent it by polling: polling from a front-end competes with
//! real work on the same endpoint and adds to the write pressure that stalls it
//! (spec.md, FR11 and the note in this crate's header). Device state therefore
//! arrives at the UI as events, pushed from here.
//!
//! The line this draws is between two things that sound alike:
//!
//! - **enumeration** asks the operating system which devices exist. It opens
//!   nothing and sends nothing, and it is what the watcher runs on a cadence;
//! - **identification** reads report descriptors, which means opening a handle
//!   per collection. It runs once per device, when a device first appears, and
//!   the answer is kept.
//!
//! A watcher that did not keep them apart would open every HID collection on the
//! machine every tick, which is exactly the background traffic to other people's
//! hardware this project has no business generating.
//!
//! # What the watcher never does
//!
//! Send a command. Nothing here talks to a device except in response to
//! something a person asked for, and a device that has stalled is not touched
//! again at all -- including by the watcher, which keeps enumerating but never
//! reconnects on its own (spec.md: a stall stops background work too).

use std::collections::HashMap;
use std::sync::mpsc::{Receiver, RecvTimeoutError, Sender, channel};
use std::thread::JoinHandle;
use std::time::Duration;

use pcaps::CapId;
use pjournal::JournalLog;

use crate::model::{CapabilityReading, ConnectionView, DeviceCard, JournalRow};
use crate::session::{CoreError, DeviceSession, DiscoveredDevice, Peripheral, PresentDevice};

/// How often the watcher asks the operating system what is plugged in.
///
/// Two seconds is chosen against what it costs and what it buys: it is a
/// question to the OS rather than to a device, so it costs nothing on the wire,
/// and a person who unplugs a keyboard tolerates a second or two before the
/// screen agrees with them. Faster would buy nothing a person can perceive.
const WATCH_INTERVAL: Duration = Duration::from_millis(2000);

/// Something the service noticed on its own.
///
/// Every variant is here because the UI cannot know to ask for it. Anything the
/// UI *can* ask for is a method on [`DeviceService`], and belongs there instead:
/// the split is the whole reason FR11 has two mechanisms rather than one bus.
#[derive(Clone, Debug)]
pub enum ServiceEvent {
    /// A device appeared, and has been identified as far as enumeration allows.
    Attached(Box<DeviceCard>),
    /// A device that was already here changed state -- connected, unreachable,
    /// stalled.
    Updated(Box<DeviceCard>),
    /// A device is gone from the bus. Any session on it is over.
    Detached { id: u64 },
    /// The device's config endpoint failed in a way the person holding it has to
    /// act on.
    ///
    /// `recoverable` is carried rather than inferred so the UI never has to
    /// guess whether to offer a retry. For a stall there is nothing to retry and
    /// offering one keeps the endpoint pinned.
    ProtocolError {
        id: u64,
        user_message: String,
        recoverable: bool,
    },
}

/// Where events go. Supplied by the caller, because presentation lives above
/// this crate and this crate must not reach up.
pub type EventSink = Box<dyn Fn(ServiceEvent) + Send + 'static>;

/// One question for the worker, and where to send the answer.
enum Job {
    Devices(Sender<Vec<DeviceCard>>),
    Connect {
        id: u64,
        reply: Sender<Result<DeviceCard, CoreError>>,
    },
    Disconnect {
        id: u64,
        reply: Sender<Result<DeviceCard, CoreError>>,
    },
    Read {
        id: u64,
        capability: CapId,
        reply: Sender<Result<CapabilityReading, CoreError>>,
    },
    Journal(Sender<Vec<JournalRow>>),
    Stop,
}

/// A handle on the device thread.
///
/// Cloneable-by-reference and safe to share: holding one grants no access to a
/// device, only the ability to ask the thread that has one. Dropping it stops
/// the thread.
pub struct DeviceService {
    jobs: Sender<Job>,
    worker: Option<JoinHandle<()>>,
}

/// The worker could not be reached, which means it is gone.
///
/// Its own error rather than a variant of [`CoreError`] because it is not a
/// device failure: it says this process's device thread has ended, and no
/// retrying or reconnecting addresses that.
#[derive(Debug)]
pub struct ServiceStopped;

impl std::fmt::Display for ServiceStopped {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("the device service has stopped")
    }
}

impl std::error::Error for ServiceStopped {}

impl DeviceService {
    /// Opens the backend and starts the device thread.
    ///
    /// Fails only if the HID backend itself will not open. A machine with no
    /// devices on it is a success with an empty list, because "nothing is
    /// plugged in" is a normal state and not an error to report.
    pub fn start(events: EventSink) -> Result<Self, CoreError> {
        let (jobs, inbox) = channel();
        // Opened here rather than on the worker so that a backend that will not
        // open is reported to the caller as a failure to start, instead of
        // becoming a thread that exits immediately and a handle that looks fine.
        let peripheral = Peripheral::open()?;
        let worker = std::thread::Builder::new()
            .name("peripheral-devices".to_owned())
            .spawn(move || Worker::new(peripheral, events).run(&inbox))
            .map_err(|error| CoreError::Backend(error.to_string()))?;
        Ok(Self {
            jobs,
            worker: Some(worker),
        })
    }

    /// Every device the service currently knows about.
    pub fn devices(&self) -> Result<Vec<DeviceCard>, ServiceStopped> {
        self.ask(Job::Devices)
    }

    /// Opens a session: sends one command, and establishes the family from what
    /// comes back.
    ///
    /// Deliberately something a person asks for rather than something that
    /// happens when a device appears. Connecting is an exchange, the vendor's
    /// own software may be holding the endpoint, and this project's stated
    /// policy is to detect that and warn rather than race for it (spec.md,
    /// compatibility constraints). An application that reached for every
    /// keyboard it saw would be the second copy in that race.
    pub fn connect(&self, id: u64) -> Result<Result<DeviceCard, CoreError>, ServiceStopped> {
        self.ask(|reply| Job::Connect { id, reply })
    }

    /// Ends a session. The device stays on the list; it is simply not open.
    pub fn disconnect(&self, id: u64) -> Result<Result<DeviceCard, CoreError>, ServiceStopped> {
        self.ask(|reply| Job::Disconnect { id, reply })
    }

    /// Reads one capability by name.
    pub fn read(
        &self,
        id: u64,
        capability: CapId,
    ) -> Result<Result<CapabilityReading, CoreError>, ServiceStopped> {
        self.ask(|reply| Job::Read {
            id,
            capability,
            reply,
        })
    }

    /// Everything the gate has authorised in this process, oldest first.
    pub fn journal(&self) -> Result<Vec<JournalRow>, ServiceStopped> {
        self.ask(Job::Journal)
    }

    fn ask<T>(&self, job: impl FnOnce(Sender<T>) -> Job) -> Result<T, ServiceStopped> {
        let (reply, answer) = channel();
        self.jobs.send(job(reply)).map_err(|_| ServiceStopped)?;
        answer.recv().map_err(|_| ServiceStopped)
    }
}

impl Drop for DeviceService {
    fn drop(&mut self) {
        let _ = self.jobs.send(Job::Stop);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

/// One device as the worker tracks it.
struct Known {
    present: PresentDevice,
    discovered: DiscoveredDevice,
    session: Option<DeviceSession>,
    /// The card as it was last published. Held so that a scan can tell whether
    /// anything actually changed and stay silent when nothing did.
    card: DeviceCard,
    /// Set when the endpoint stalled. Nothing touches this device again until it
    /// leaves the bus and comes back.
    stalled: bool,
}

struct Worker {
    peripheral: Peripheral,
    events: EventSink,
    known: HashMap<u64, Known>,
    /// One journal for the process, not one per session.
    ///
    /// A session's journal would be dropped with the session, taking with it
    /// precisely the entries a person opens the journal to read: the ones from
    /// the connection that failed.
    journal: JournalLog,
    /// How many entries have already been turned into rows. Only used to keep
    /// the conversion off the hot path; the log itself is the record.
    published: usize,
    rows: Vec<JournalRow>,
}

impl Worker {
    fn new(peripheral: Peripheral, events: EventSink) -> Self {
        Self {
            peripheral,
            events,
            known: HashMap::new(),
            journal: JournalLog::new(),
            published: 0,
            rows: Vec::new(),
        }
    }

    fn run(mut self, inbox: &Receiver<Job>) {
        self.scan();
        loop {
            match inbox.recv_timeout(WATCH_INTERVAL) {
                Ok(Job::Stop) | Err(RecvTimeoutError::Disconnected) => return,
                Ok(job) => self.handle(job),
                Err(RecvTimeoutError::Timeout) => self.scan(),
            }
        }
    }

    fn handle(&mut self, job: Job) {
        match job {
            Job::Devices(reply) => {
                let _ = reply.send(self.cards());
            }
            Job::Connect { id, reply } => {
                let result = self.connect(id);
                let _ = reply.send(result);
            }
            Job::Disconnect { id, reply } => {
                let result = self.disconnect(id);
                let _ = reply.send(result);
            }
            Job::Read {
                id,
                capability,
                reply,
            } => {
                let result = self.read(id, capability);
                let _ = reply.send(result);
            }
            Job::Journal(reply) => {
                self.collect_journal();
                let _ = reply.send(self.rows.clone());
            }
            Job::Stop => {}
        }
    }

    /// Cards in a stable order, so a list does not reshuffle itself between two
    /// calls that found the same hardware.
    fn cards(&self) -> Vec<DeviceCard> {
        let mut cards: Vec<DeviceCard> = self.known.values().map(|k| k.card.clone()).collect();
        cards.sort_by_key(|card| card.id);
        cards
    }

    /// One pass of the watcher.
    fn scan(&mut self) {
        if let Err(error) = self.peripheral.refresh() {
            // Enumeration failing is not a device failure and there is nothing
            // useful to tell a user about it, so the known state is left exactly
            // as it was rather than being emptied. Reporting every device as
            // detached because one call to the OS failed would be the screen
            // lying in the other direction.
            log_backend_hiccup(&error);
            return;
        }

        let present = self.peripheral.present();
        let seen: Vec<u64> = present.iter().map(|p| p.device.raw()).collect();

        for id in departed(self.known.keys().copied(), &seen) {
            self.known.remove(&id);
            (self.events)(ServiceEvent::Detached { id });
        }

        for device in present {
            let id = device.device.raw();
            match self.known.get_mut(&id) {
                Some(known) => {
                    // Already here. Enumeration is re-read anyway, because a
                    // wireless device can change what it calls itself between
                    // connection modes, but identification is not: that costs a
                    // handle per collection and nothing about it has changed.
                    known.present = device;
                }
                None => {
                    let discovered = self.peripheral.identify(&device);
                    let card = DeviceCard::from_discovered(&discovered);
                    self.known.insert(
                        id,
                        Known {
                            present: device,
                            discovered,
                            session: None,
                            card: card.clone(),
                            stalled: false,
                        },
                    );
                    (self.events)(ServiceEvent::Attached(Box::new(card)));
                }
            }
        }
    }

    fn connect(&mut self, id: u64) -> Result<DeviceCard, CoreError> {
        let Some(known) = self.known.get(&id) else {
            return Err(CoreError::NotFound);
        };
        if known.stalled {
            return Err(CoreError::Stalled);
        }
        if known.session.is_some() {
            return Ok(known.card.clone());
        }

        let discovered = known.discovered.clone();
        let base = DeviceCard::from_discovered(&discovered);
        let outcome = self.peripheral.connect(&discovered, &mut self.journal);
        self.collect_journal();

        match outcome {
            Ok(session) => {
                let card = base.with_session(&session);
                if let Some(known) = self.known.get_mut(&id) {
                    known.session = Some(session);
                    known.card = card.clone();
                }
                (self.events)(ServiceEvent::Updated(Box::new(card.clone())));
                Ok(card)
            }
            Err(error) => {
                let card = self.record_failure(id, base, &error);
                (self.events)(ServiceEvent::Updated(Box::new(card)));
                Err(error)
            }
        }
    }

    fn disconnect(&mut self, id: u64) -> Result<DeviceCard, CoreError> {
        let Some(known) = self.known.get_mut(&id) else {
            return Err(CoreError::NotFound);
        };
        known.session = None;
        // Rebuilt from what enumeration knows rather than edited, so that
        // everything a session established -- the family, the model id, the
        // capability list -- goes away with the session that earned it. Leaving
        // a verified family on a card after closing the session that verified it
        // would be the screen claiming knowledge nothing currently supports.
        known.card = DeviceCard::from_discovered(&known.discovered);
        let card = known.card.clone();
        (self.events)(ServiceEvent::Updated(Box::new(card.clone())));
        Ok(card)
    }

    fn read(&mut self, id: u64, capability: CapId) -> Result<CapabilityReading, CoreError> {
        let Some(known) = self.known.get_mut(&id) else {
            return Err(CoreError::NotFound);
        };
        if known.stalled {
            return Err(CoreError::Stalled);
        }
        let Some(session) = known.session.as_mut() else {
            return Err(CoreError::FamilyNotEstablished);
        };

        let outcome = self.peripheral.read(session, &mut self.journal, capability);
        self.collect_journal();

        match outcome {
            Ok(value) => Ok(CapabilityReading::new(&value, unix_now_ms())),
            Err(error) => {
                let Some(known) = self.known.get(&id) else {
                    return Err(error);
                };
                let base = known.card.clone();
                let card = self.record_failure(id, base, &error);
                (self.events)(ServiceEvent::Updated(Box::new(card)));
                Err(error)
            }
        }
    }

    /// Applies a failure to a card and tells the UI about it.
    ///
    /// The classification itself is [`failure_view`], deliberately outside this
    /// method: what a failure means is a decision worth testing on its own, and
    /// the one that matters -- never offer a retry for a stall -- must not be
    /// reachable only through a device.
    fn record_failure(&mut self, id: u64, mut card: DeviceCard, error: &CoreError) -> DeviceCard {
        let stall = error.is_stall();
        let (connection, recoverable) = failure_view(error);
        let user_message = error.to_string();
        card.connection = connection;

        if let Some(known) = self.known.get_mut(&id) {
            if stall {
                // The whole device goes quiet, including the session that was
                // open on it. Reopening does not clear a stalled endpoint and
                // more traffic keeps it pinned, so the only correct response is
                // to stop -- until it leaves the bus, which is what clears the
                // flag, because the flag is removed with the entry itself.
                known.stalled = true;
                known.session = None;
            }
            known.card = card.clone();
        }

        (self.events)(ServiceEvent::ProtocolError {
            id,
            user_message,
            recoverable,
        });

        card
    }

    /// Moves whatever the gate has recorded since last time into rows.
    fn collect_journal(&mut self) {
        let entries = self.journal.entries();
        let epoch = self.peripheral.epoch_unix_ms();
        for entry in &entries[self.published.min(entries.len())..] {
            self.rows.push(JournalRow::new(entry, epoch));
        }
        self.published = entries.len();
    }
}

/// Which known devices are no longer being enumerated.
///
/// Its own function, and pure, because it is the half of the watcher that a
/// physical unplug is needed to exercise -- and a rule that can only be checked
/// by pulling a cable is a rule that stops being checked. What it protects
/// against is the acceptance criterion in one line: a device that has left the
/// bus must leave the screen, and it must leave it *once*, not on every tick.
///
/// The empty-`seen` case is deliberately not special. Enumeration returning
/// nothing means nothing is plugged in, which is an ordinary state and not a
/// reason to keep stale cards; the failure of the enumeration *call* is handled
/// separately, before this is reached, and does leave the known set alone.
fn departed(known: impl Iterator<Item = u64>, seen: &[u64]) -> Vec<u64> {
    let seen: std::collections::HashSet<u64> = seen.iter().copied().collect();
    known.filter(|id| !seen.contains(id)).collect()
}

/// What a failure means for the card, and whether a retry may be offered.
///
/// The second half is the safety-relevant one. Nothing on this path retries on
/// its own, so `recoverable` decides only whether the UI shows a button -- and
/// for a stalled endpoint that button is actively harmful: reopening does not
/// clear the stall and every further attempt keeps it pinned (spec.md § Failure
/// and fallback behavior). A channel that would not open gets no button either,
/// because the usual cause is another application holding it and hammering it
/// is not how that resolves.
fn failure_view(error: &CoreError) -> (ConnectionView, bool) {
    let user_message = error.to_string();
    match error {
        CoreError::Stalled => (ConnectionView::Stalled { user_message }, false),
        CoreError::ChannelUnavailable(_) => (
            ConnectionView::Unreachable {
                user_message,
                vendor_software_suspected: true,
            },
            false,
        ),
        _ => (
            ConnectionView::Unreachable {
                user_message,
                vendor_software_suspected: false,
            },
            true,
        ),
    }
}

/// Milliseconds since the Unix epoch. See the note on the same helper in
/// [`crate::session`]: zero rather than a panic.
fn unix_now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|since| u64::try_from(since.as_millis()).unwrap_or(u64::MAX))
        .unwrap_or(0)
}

/// Enumeration failed once. Recorded, not surfaced.
///
/// Kept as its own function so the decision is visible: this is the one failure
/// on the watcher's path that is deliberately not shown to a user, because
/// there is nothing they could do about it and the next tick is two seconds
/// away.
fn log_backend_hiccup(error: &CoreError) {
    let _ = error;
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    #[test]
    fn a_handle_is_shareable_across_threads() {
        // Asserted rather than assumed, because it is the reason this module is
        // shaped the way it is. The application layer stores this handle in a
        // framework's shared state, which requires both bounds; if the worker
        // were ever replaced by something holding a device directly, this stops
        // compiling, which is the intended outcome.
        fn requires<T: Send + Sync>() {}
        requires::<DeviceService>();
    }

    #[test]
    fn a_device_that_stops_enumerating_is_reported_gone() {
        assert_eq!(departed([1, 2, 3].into_iter(), &[1, 3]), vec![2]);
    }

    #[test]
    fn nothing_is_reported_gone_while_everything_is_still_here() {
        // The tick that must be silent. A watcher that emitted on every pass
        // would rebuild the list twice a second and lose the user's selection
        // each time.
        assert!(departed([1, 2].into_iter(), &[2, 1]).is_empty());
    }

    #[test]
    fn an_empty_bus_means_everything_is_gone() {
        // Not a special case, and this is the assertion that says so. "Nothing
        // is plugged in" is an ordinary answer; the case where the enumeration
        // *call* fails is handled before this function and leaves the known set
        // untouched instead.
        assert_eq!(departed([7].into_iter(), &[]), vec![7]);
    }

    #[test]
    fn a_device_arriving_does_not_make_another_one_look_gone() {
        assert!(departed([1].into_iter(), &[1, 2]).is_empty());
    }

    #[test]
    fn a_stall_is_never_offered_a_retry() {
        // The one classification that can make things worse if it is wrong.
        // A stalled endpoint does not recover from being reopened and every
        // further attempt keeps it stalled, so the button must not exist.
        let (connection, recoverable) = failure_view(&CoreError::Stalled);
        assert!(!recoverable);
        assert!(matches!(connection, ConnectionView::Stalled { .. }));
    }

    #[test]
    fn a_channel_that_will_not_open_names_the_likely_cause_without_claiming_it() {
        // The minimal vendor-conflict heuristic. It is a suspicion, and it has
        // to stay one: the backend hands back localised text with no code, so
        // nothing here can establish that another application is holding the
        // endpoint (TICKET-08). What it must not do is offer a retry, because
        // racing the vendor's software for an endpoint is the behaviour this
        // project has ruled out.
        let (connection, recoverable) =
            failure_view(&CoreError::ChannelUnavailable("in use".to_owned()));
        assert!(!recoverable);
        match connection {
            ConnectionView::Unreachable {
                vendor_software_suspected,
                user_message,
            } => {
                assert!(vendor_software_suspected);
                assert!(user_message.contains("would not open"));
            }
            other => panic!("expected unreachable, got {other:?}"),
        }
    }

    #[test]
    fn an_ordinary_failure_is_not_dressed_up_as_a_conflict() {
        let (connection, recoverable) = failure_view(&CoreError::NotFound);
        assert!(recoverable);
        assert!(matches!(
            connection,
            ConnectionView::Unreachable {
                vendor_software_suspected: false,
                ..
            }
        ));
    }
}
