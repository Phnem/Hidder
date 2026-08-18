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

use std::time::Duration;

use pcaps::{CapId, Capability, Confidence, Unavailable};
use pjournal::JournalLog;
use pproto::aula_bytech_engine::{AulaBytechEngine, ConfigEndpoint};
use pproto::aula_bytech_io::Exchange;
use pregistry::{Identification, Registry};
use psafety::{MonotonicClock, SafetyGate};
use ptransport::{DeviceId, Hid, HidCollection, ProbeChannel};

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
    /// Nothing has established which protocol this device speaks, so there is no
    /// such thing as a safe command to send it.
    FamilyNotEstablished,
    /// The device was reached and the operation was refused or failed. Carries
    /// the reason in the terms the safety layer used.
    Operation(String),
}

impl std::fmt::Display for CoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CoreError::Backend(message) => write!(f, "hid backend: {message}"),
            CoreError::NotFound => f.write_str("no such device is connected"),
            CoreError::NoConfigEndpoint => {
                f.write_str("this device exposes no configuration channel we recognise")
            }
            CoreError::FamilyNotEstablished => {
                f.write_str("this device's protocol family has not been established")
            }
            CoreError::Operation(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for CoreError {}

/// A device as the Devices screen should see it.
///
/// Everything here comes from enumeration. Nothing in this type required
/// talking to the firmware, which is what makes it safe to build for every
/// connected device including ones this project knows nothing about.
#[derive(Clone, Debug)]
pub struct DiscoveredDevice {
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
    /// Three answers, computed separately and never averaged.
    pub identification: Identification,
    /// The configuration channel, if one was recognised.
    pub config: Option<ConfigEndpoint>,
    /// OS path of the collection to open. A transport detail, kept private so
    /// nothing above can hold one.
    path: Option<String>,
}

impl DiscoveredDevice {
    /// Whether this device can be connected to at all.
    pub fn is_connectable(&self) -> bool {
        self.config.is_some()
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
}

impl Peripheral {
    pub fn open() -> Result<Self, CoreError> {
        Ok(Self {
            hid: Hid::open().map_err(|error| CoreError::Backend(error.to_string()))?,
            registry: Registry::builtin(),
        })
    }

    /// Every connected device, identified as far as enumeration allows.
    ///
    /// Note what this does **not** do: it sends nothing. A device appears here
    /// with `family: None` until something has spoken to it, and that is the
    /// default-deny position rather than a gap.
    pub fn discover(&self) -> Vec<DiscoveredDevice> {
        let collections = self.hid.enumerate();
        let endpoint = AulaBytechEngine::config_endpoint();

        let mut devices: Vec<DiscoveredDevice> = Vec::new();
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

            let observation = crate::observe::from_collections(&self.hid, &siblings);
            devices.push(DiscoveredDevice {
                device: DeviceId::new(
                    u64::from(collection.vendor_id) << 16 | u64::from(collection.product_id),
                ),
                vendor_id: collection.vendor_id,
                product_id: collection.product_id,
                product: collection.product.clone(),
                manufacturer: collection.manufacturer.clone(),
                serial_present: collection.serial_number.is_some(),
                identification: self.registry.identify(&observation),
                config: config.map(|_| endpoint),
                path: config.map(|c| c.path.clone()),
            });
        }
        devices
    }

    /// Opens a device and establishes what it speaks.
    ///
    /// The order matters and is the whole shape of the vertical slice:
    /// enumeration identifies the product, then **one command** establishes the
    /// protocol family, and only then is any capability readable. A family
    /// established by asking the device is the only kind that authorises
    /// anything, which is why this is a connect step and not a lookup.
    pub fn connect(&self, discovered: &DiscoveredDevice) -> Result<DeviceSession, CoreError> {
        let path = discovered
            .path
            .as_deref()
            .ok_or(CoreError::NoConfigEndpoint)?;
        let collection = self
            .hid
            .enumerate()
            .into_iter()
            .find(|c| c.path == path)
            .ok_or(CoreError::NotFound)?;

        let channel = ProbeChannel::open(&self.hid, &collection, discovered.device)
            .map_err(|e| CoreError::Backend(e.to_string()))?;

        let mut journal = JournalLog::new();
        let mut gate = SafetyGate::new(
            Exchange::new(channel, EXCHANGE_TIMEOUT),
            &mut journal,
            MonotonicClock::default(),
        );

        // The family is not yet established, so this first command is authorised
        // on the strength of the device being *identified* rather than verified.
        // That is the bootstrap of the family axis and it is deliberately the
        // narrowest possible one: a single read whose meaning was verified on
        // hardware, whose answer is validated before it becomes a value.
        gate.identify_device(
            discovered.device,
            AulaBytechEngine::family(),
            pcaps::FamilyConfidence::established(Confidence::Candidate),
        );
        let model_id = AulaBytechEngine::read_model_id(&mut gate, discovered.device, None)
            .map_err(|e| CoreError::Operation(e.to_string()))?;

        // It answered correctly, so the family is now established by exchange
        // rather than by expectation, and the registry is told in those terms.
        let evidence = AulaBytechEngine::verified_exchange_evidence();
        let identification = self.registry.identify_with(
            &crate::observe::from_collections(&self.hid, &[&collection]),
            Some(evidence),
        );
        gate.identify_device(
            discovered.device,
            AulaBytechEngine::family(),
            identification.family.confidence,
        );

        drop(gate);
        Ok(DeviceSession {
            device: discovered.device,
            model_id: model_id.raw(),
            identification,
            journal,
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
            .map_err(|e| CoreError::Backend(e.to_string()))
    }

    /// Reads one capability, by name.
    ///
    /// The call the UI makes. It names a [`CapId`] and nothing else: no command,
    /// no opcode, no key id, no scale.
    pub fn read(&self, session: &mut DeviceSession, id: CapId) -> Result<Capability, CoreError> {
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
            &mut session.journal,
            MonotonicClock::default(),
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
            .map_err(|e| CoreError::Operation(e.to_string())),
        }
    }
}

/// An open conversation with one device.
///
/// Holds what has been established about it and the record of everything done
/// to it. Deliberately holds no handle yet -- see [`Peripheral::channel`].
pub struct DeviceSession {
    device: DeviceId,
    model_id: u64,
    identification: Identification,
    journal: JournalLog,
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

    /// What this application has done to this device, in order.
    pub fn journal(&self) -> &JournalLog {
        &self.journal
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
