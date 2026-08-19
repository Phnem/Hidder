//! Discovery, identification and capability reads, in one place a UI can hold.
//!
//! This is the layer the application talks to, and the reason it exists is the
//! rule in this crate's header: **the UI never reaches a device.** It asks this
//! model, and this model owns the only path down.
//!
//! # What a caller cannot do from here
//!
//! - name a command;
//! - name an opcode, a report id, or a key id;
//! - reach a HID handle, or the gate's sink;
//! - read a capability that no engine has earned.
//!
//! A caller names a [`pcaps::CapId`] and gets a value or a reason there is none.
//! Everything between is arranged below.
//!
//! # Two questions, priced differently
//!
//! Asking *which devices are here* costs nothing: enumeration answers it from
//! the operating system's own registry. Asking *what a device is* costs a handle
//! open per collection, because the fingerprint is computed from report
//! descriptors and a descriptor has to be read off the device node.
//!
//! [`Peripheral::present`] and [`Peripheral::identify`] are separate for that
//! reason. Something watching for a keyboard being unplugged runs the first one
//! repeatedly and the second one only for a device it has not seen before; a
//! watcher built on [`Peripheral::discover`] alone would open every HID
//! collection on the machine on every tick, which is background traffic to
//! hardware nobody asked us to touch.

use std::time::Duration;

use pcaps::{CapId, Capability, Confidence, Unavailable};
use pjournal::JournalLog;
use pproto::aula_bytech_engine::{AulaBytechEngine, ConfigEndpoint};
use pproto::aula_bytech_io::Exchange;
use pregistry::{Identification, Registry};
use psafety::{MonotonicClock, ReadError, SafetyGate};
use ptransport::{DeviceId, Hid, HidCollection, ProbeChannel, TransportError};

/// How long one exchange may take before it is treated as unanswered.
///
/// Comfortably above the 2.4–2.8 ms this family's reads have measured, and well
/// under anything a person would notice. Not a retry budget: nothing on this
/// path retries.
const EXCHANGE_TIMEOUT: Duration = Duration::from_millis(1500);

/// Why a device could not be reached or read.
#[derive(Debug)]
pub enum CoreError {
    /// The HID backend itself is unavailable.
    Backend(String),
    /// No connected device matches.
    NotFound,
    /// The device is here, but nothing on it looks like a configuration channel
    /// this project knows how to speak on.
    NoConfigEndpoint,
    /// The configuration channel is there and would not open.
    ///
    /// Its own variant rather than a `Backend` string because it is the one
    /// failure a person can usually act on: the commonest cause is another
    /// application already holding the endpoint. The message is the backend's
    /// and is opaque -- see [`CoreError::Backend`] and TICKET-08 on why nothing
    /// matches on its contents.
    ChannelUnavailable(String),
    /// Nothing has established which protocol this device speaks, so there is no
    /// such thing as a safe command to send it.
    FamilyNotEstablished,
    /// The device's config endpoint wedged. Terminal until it is physically
    /// reconnected: reopening does not clear it and more traffic keeps it
    /// pinned (spec.md § Failure and fallback behavior).
    Stalled,
    /// The device was reached and the operation was refused or failed. Carries
    /// the reason in the terms the safety layer used.
    Operation(String),
}

impl CoreError {
    /// Whether this is the failure after which the device must go quiet.
    pub fn is_stall(&self) -> bool {
        matches!(self, CoreError::Stalled)
    }
}

impl std::fmt::Display for CoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CoreError::Backend(message) => write!(f, "hid backend: {message}"),
            CoreError::NotFound => f.write_str("no such device is connected"),
            CoreError::NoConfigEndpoint => {
                f.write_str("this device exposes no configuration channel we recognise")
            }
            CoreError::ChannelUnavailable(message) => {
                write!(f, "the configuration channel would not open: {message}")
            }
            CoreError::FamilyNotEstablished => {
                f.write_str("this device's protocol family has not been established")
            }
            CoreError::Stalled => f.write_str(
                "the device's config endpoint has stalled; unplug it, wait ten seconds, plug it back in",
            ),
            CoreError::Operation(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for CoreError {}

/// Turns one engine call's failure into this layer's vocabulary.
///
/// The stall is pulled out by type rather than left inside a formatted string,
/// because the kill switch above has to branch on it and a message it would have
/// to match on is the localised-text trap from TICKET-08 all over again.
fn from_read_error<R: std::fmt::Debug>(error: ReadError<R>) -> CoreError {
    match error {
        ReadError::Transport(TransportError::EndpointStalled) => CoreError::Stalled,
        other => CoreError::Operation(other.to_string()),
    }
}

/// A device as enumeration alone reports it.
///
/// Nothing here required opening a handle, which is what makes it cheap enough
/// to ask for repeatedly. It is deliberately not enough to identify anything:
/// the fingerprint needs descriptors, and reading those is [`Peripheral::identify`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PresentDevice {
    pub device: DeviceId,
    pub vendor_id: u16,
    pub product_id: u16,
    /// What the device calls itself. Not authoritative: nine AULA models share
    /// one VID:PID and the vendor's own software cannot tell them apart either.
    pub product: Option<String>,
    pub manufacturer: Option<String>,
    /// Whether a serial number exists -- never its value (spec.md, domain
    /// rules; the policy is general, not AULA-specific).
    pub serial_present: bool,
    /// The configuration channel, if the shape of one was recognised. Says
    /// nothing about whether it opens or answers.
    pub config: Option<ConfigEndpoint>,
    /// OS paths of this device's collections. A transport detail, kept private
    /// so nothing above can hold one.
    paths: Vec<String>,
    /// The collection to open for configuration, when there is one.
    config_path: Option<String>,
}

impl PresentDevice {
    /// Whether this device exposes a configuration channel we recognise.
    pub fn is_connectable(&self) -> bool {
        self.config.is_some()
    }

    /// Something to put on a card for a device nobody recognises.
    ///
    /// Every device gets a label, including one this project has never heard of.
    /// The alternative is a blank row, and a blank row is how a user concludes
    /// the application is broken rather than that the device is unknown.
    pub fn label(&self) -> String {
        match (&self.manufacturer, &self.product) {
            (Some(vendor), Some(product)) => format!("{vendor} {product}"),
            (None, Some(product)) => product.clone(),
            (Some(vendor), None) => format!("{vendor} device"),
            (None, None) => format!("{:04X}:{:04X}", self.vendor_id, self.product_id),
        }
    }
}

/// A device as the Devices screen should see it.
///
/// Everything here comes from enumeration and from report descriptors. Nothing
/// in this type required talking to the firmware, which is what makes it safe to
/// build for every connected device including ones this project knows nothing
/// about.
#[derive(Clone, Debug)]
pub struct DiscoveredDevice {
    pub present: PresentDevice,
    /// Three answers, computed separately and never averaged.
    pub identification: Identification,
}

impl DiscoveredDevice {
    pub fn device(&self) -> DeviceId {
        self.present.device
    }

    pub fn is_connectable(&self) -> bool {
        self.present.is_connectable()
    }

    /// What is known about a capability before anything has been read.
    ///
    /// Answers with a reason rather than a bare `false`, because "we have no
    /// verified command" and "this board lacks the feature" are different
    /// statements and a UI that renders them alike is lying about one of them.
    pub fn capability_state(&self, id: CapId) -> Result<(), Unavailable> {
        match id {
            CapId::HeActuation => {
                if self.identification.family.family == Some(AulaBytechEngine::family()) {
                    Ok(())
                } else {
                    Err(Unavailable::NoVerifiedCommand)
                }
            }
        }
    }
}

/// The HID backend, opened once.
///
/// Owns the enumeration side. Sessions own the I/O side, and there is exactly
/// one session per open device (spec.md FR10).
pub struct Peripheral {
    hid: Hid,
    registry: Registry,
    /// One clock for the whole process, copied into every gate.
    ///
    /// A gate is built per operation on this path, so a gate that made its own
    /// clock would stamp every journal entry with a time near zero. See
    /// [`psafety::MonotonicClock`].
    clock: MonotonicClock,
    /// Unix time at the moment `clock` was zeroed, so a monotonic offset can be
    /// turned into a time a person recognises without the journal itself having
    /// to depend on a wall clock that can jump.
    epoch_unix_ms: u64,
}

impl Peripheral {
    pub fn open() -> Result<Self, CoreError> {
        Ok(Self {
            hid: Hid::open().map_err(|error| CoreError::Backend(error.to_string()))?,
            registry: Registry::builtin(),
            clock: MonotonicClock::default(),
            epoch_unix_ms: unix_now_ms(),
        })
    }

    /// Unix time corresponding to zero on the journal's clock.
    pub fn epoch_unix_ms(&self) -> u64 {
        self.epoch_unix_ms
    }

    /// Re-asks the operating system which devices exist.
    ///
    /// Must be called before [`Peripheral::present`] if the answer is expected
    /// to change: the backend's device list is built once, when the handle is
    /// opened. Opens nothing and sends nothing.
    pub fn refresh(&mut self) -> Result<(), CoreError> {
        self.hid
            .refresh()
            .map_err(|error| CoreError::Backend(error.to_string()))
    }

    /// Every connected device, from enumeration only.
    ///
    /// One entry per VID:PID, because that is as far as enumeration alone can
    /// separate devices. Two identical boards therefore appear as one, which is
    /// a known limit rather than an oversight -- see this crate's notes.
    pub fn present(&self) -> Vec<PresentDevice> {
        let collections = self.hid.enumerate();
        let endpoint = AulaBytechEngine::config_endpoint();

        let mut devices: Vec<PresentDevice> = Vec::new();
        for collection in &collections {
            let key = (collection.vendor_id, collection.product_id);
            if devices.iter().any(|d| (d.vendor_id, d.product_id) == key) {
                continue;
            }
            let siblings: Vec<&HidCollection> = collections
                .iter()
                .filter(|c| (c.vendor_id, c.product_id) == key)
                .collect();

            let config = siblings
                .iter()
                .find(|c| c.usage_page == endpoint.usage_page && c.usage == endpoint.usage);

            devices.push(PresentDevice {
                device: DeviceId::new(
                    u64::from(collection.vendor_id) << 16 | u64::from(collection.product_id),
                ),
                vendor_id: collection.vendor_id,
                product_id: collection.product_id,
                product: collection.product.clone(),
                manufacturer: collection.manufacturer.clone(),
                serial_present: collection.serial_number.is_some(),
                config: config.map(|_| endpoint),
                paths: siblings.iter().map(|c| c.path.clone()).collect(),
                config_path: config.map(|c| c.path.clone()),
            });
        }
        devices
    }

    /// Identifies one present device, reading its report descriptors.
    ///
    /// The expensive half: it opens each of the device's collections to read a
    /// descriptor, and callers are expected to keep the answer rather than ask
    /// again on a timer.
    ///
    /// Note what this does **not** do: it sends nothing. A device comes back
    /// with `family: None` until something has spoken to it, and that is the
    /// default-deny position rather than a gap.
    pub fn identify(&self, present: &PresentDevice) -> DiscoveredDevice {
        let collections = self.hid.enumerate();
        let mine: Vec<&HidCollection> = collections
            .iter()
            .filter(|c| present.paths.iter().any(|path| path == &c.path))
            .collect();
        let observation = crate::observe::from_collections(&self.hid, &mine);
        DiscoveredDevice {
            present: present.clone(),
            identification: self.registry.identify(&observation),
        }
    }

    /// Every connected device, identified as far as enumeration allows.
    pub fn discover(&self) -> Vec<DiscoveredDevice> {
        self.present()
            .iter()
            .map(|present| self.identify(present))
            .collect()
    }

    /// Opens a device and establishes what it speaks.
    ///
    /// The order matters and is the whole shape of the vertical slice:
    /// enumeration identifies the product, then **one command** establishes the
    /// protocol family, and only then is any capability readable. A family
    /// established by asking the device is the only kind that authorises
    /// anything, which is why this is a connect step and not a lookup.
    ///
    /// The journal is the caller's rather than the session's, and that is not
    /// bookkeeping. A connection that is refused, or that fails partway, is
    /// exactly the event a person opens the journal to understand -- and a
    /// journal owned by the session would be dropped along with it, taking those
    /// entries with it. Everything the gate authorised on the way to a failure
    /// survives here.
    pub fn connect(
        &self,
        discovered: &DiscoveredDevice,
        journal: &mut JournalLog,
    ) -> Result<DeviceSession, CoreError> {
        let path = discovered
            .present
            .config_path
            .as_deref()
            .ok_or(CoreError::NoConfigEndpoint)?;
        let collection = self
            .hid
            .enumerate()
            .into_iter()
            .find(|c| c.path == path)
            .ok_or(CoreError::NotFound)?;

        let device = discovered.device();
        let channel = ProbeChannel::open(&self.hid, &collection, device)
            .map_err(|e| CoreError::ChannelUnavailable(e.to_string()))?;

        let mut gate = SafetyGate::new(
            Exchange::new(channel, EXCHANGE_TIMEOUT),
            &mut *journal,
            self.clock,
        );

        // The family is not yet established, so this first command is authorised
        // on the strength of the device being *identified* rather than verified.
        // That is the bootstrap of the family axis and it is deliberately the
        // narrowest possible one: a single read whose meaning was verified on
        // hardware, whose answer is validated before it becomes a value.
        gate.identify_device(
            device,
            AulaBytechEngine::family(),
            pcaps::FamilyConfidence::established(Confidence::Candidate),
        );
        let model_id =
            AulaBytechEngine::read_model_id(&mut gate, device, None).map_err(from_read_error)?;

        // It answered correctly, so the family is now established by exchange
        // rather than by expectation, and the registry is told in those terms.
        //
        // The observation is rebuilt from **all** of this device's collections,
        // not from the one that was just spoken to. Only the family axis learned
        // anything here; the structural and product axes are about the shape of
        // the device and which product it is, and an exchange cannot change
        // either. Observing one collection would recompute those two from a
        // seventh of the evidence and report a *worse* answer than before the
        // connection -- a screen where talking to a device makes the application
        // less sure what it is.
        let evidence = AulaBytechEngine::verified_exchange_evidence();
        let all = self.hid.enumerate();
        let mine: Vec<&HidCollection> = all
            .iter()
            .filter(|c| discovered.present.paths.iter().any(|path| path == &c.path))
            .collect();
        let identification = self.registry.identify_with(
            &crate::observe::from_collections(&self.hid, &mine),
            Some(evidence),
        );

        drop(gate);
        Ok(DeviceSession {
            device,
            model_id: model_id.raw(),
            identification,
            channel_path: path.to_string(),
        })
    }

    /// Reopens the channel for one session's operations.
    ///
    /// A concession recorded rather than hidden: a session ought to own its
    /// handle for its whole life on a dedicated worker thread (spec.md FR10),
    /// and that is `DeviceSession` proper, which the analog stream needs and
    /// TICKET-15 builds. Until then a capability read opens, exchanges and
    /// closes, which is correct but not the final shape -- and it is why the
    /// rate limiter lives on a gate built per operation here, a limitation this
    /// method's callers must not rely on.
    fn channel(&self, session: &DeviceSession) -> Result<ProbeChannel, CoreError> {
        let collection = self
            .hid
            .enumerate()
            .into_iter()
            .find(|c| c.path == session.channel_path)
            .ok_or(CoreError::NotFound)?;
        ProbeChannel::open(&self.hid, &collection, session.device)
            .map_err(|e| CoreError::ChannelUnavailable(e.to_string()))
    }

    /// Reads one capability, by name.
    ///
    /// The call the UI makes. It names a [`CapId`] and nothing else: no command,
    /// no opcode, no key id, no scale.
    pub fn read(
        &self,
        session: &mut DeviceSession,
        journal: &mut JournalLog,
        id: CapId,
    ) -> Result<Capability, CoreError> {
        if !session
            .identification
            .family
            .confidence
            .value()
            .eq(&Confidence::Verified)
            && session.identification.family.family.is_none()
        {
            return Err(CoreError::FamilyNotEstablished);
        }

        let channel = self.channel(session)?;
        let mut gate = SafetyGate::new(
            Exchange::new(channel, EXCHANGE_TIMEOUT),
            &mut *journal,
            self.clock,
        );
        gate.identify_device(
            session.device,
            AulaBytechEngine::family(),
            session.identification.family.confidence,
        );

        match id {
            CapId::HeActuation => AulaBytechEngine::read_actuation(
                &mut gate,
                session.device,
                AulaBytechEngine::travel_scale(),
                None,
            )
            .map_err(from_read_error),
        }
    }
}

/// Milliseconds since the Unix epoch, or zero if the system clock predates it.
///
/// Zero rather than a panic: a journal with an implausible timestamp is a bad
/// record, and an application that will not start because a clock is odd is a
/// worse product.
fn unix_now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|since| u64::try_from(since.as_millis()).unwrap_or(u64::MAX))
        .unwrap_or(0)
}

/// An open conversation with one device.
///
/// Holds what has been established about it. Deliberately holds no handle yet --
/// see [`Peripheral::channel`] -- and no journal: what was done to a device
/// outlives the session that did it.
pub struct DeviceSession {
    device: DeviceId,
    model_id: u64,
    identification: Identification,
    channel_path: String,
}

impl DeviceSession {
    pub fn device(&self) -> DeviceId {
        self.device
    }

    /// The model identifier the device reported.
    ///
    /// A model, not a unit: every HERO 84 HE returns the same value, so it
    /// carries no per-unit entropy and is not a serial number.
    pub fn model_id(&self) -> u64 {
        self.model_id
    }

    pub fn identification(&self) -> &Identification {
        &self.identification
    }

    /// Which capabilities are readable here, and why the others are not.
    pub fn capabilities(&self) -> Vec<(CapId, Result<(), Unavailable>)> {
        CapId::all()
            .iter()
            .map(|id| {
                let state = if self.identification.family.family == Some(AulaBytechEngine::family())
                {
                    Ok(())
                } else {
                    Err(Unavailable::NoVerifiedCommand)
                };
                (*id, state)
            })
            .collect()
    }

    /// Whether the protocol family is established well enough to authorise a
    /// write, were there one to make.
    ///
    /// True on our board since TICKET-12, and on its own it means less than it
    /// sounds. It is one of two conditions and it is the weaker one to reason
    /// about: it says the family axis reached `Verified`, not that anything is
    /// writable. See [`DeviceSession::writable_commands`].
    pub fn family_permits_write(&self) -> bool {
        self.identification.permits_write()
    }

    /// How many write commands exist for this device's family.
    ///
    /// Zero, in every build so far, and the number comes from the generated ACL
    /// rather than from a constant here -- so it stops being zero exactly when
    /// somebody adds a reviewed `safe_write` entry, and not a moment earlier.
    ///
    /// This is the honest half of "can this device be written to". A UI that
    /// showed only [`DeviceSession::family_permits_write`] would report
    /// "permitted" on a build that has no way to write anything, which is a
    /// worse lie than reporting "not permitted" would be.
    pub fn writable_commands(&self) -> usize {
        let Some(family) = self.identification.family.family else {
            return 0;
        };
        psafety::SafeCommandId::all()
            .filter(|id| id.family() == family && id.class().writes())
            .count()
    }

    /// Whether anything can actually be written to this device right now.
    ///
    /// Both conditions, which is what a control should be enabled on.
    pub fn is_writable(&self) -> bool {
        self.family_permits_write() && self.writable_commands() > 0
    }
}
