/**
 * What the window knows, and the rules for changing it.
 *
 * Pure: no React, no `invoke`, no clock. Everything here is a function of its
 * arguments, which is what makes the rules below testable without a device, a
 * browser or a running backend — and those rules are the part of this
 * application that is allowed to be wrong in a way a user would not notice.
 *
 * # The rule this module exists to hold
 *
 * Never show a control with no confirmed command behind it (spec.md, hard
 * requirement). Every decision that could break it is a named function here
 * rather than a condition inside a component, because a condition inside a
 * component is one somebody copies into the next component slightly differently.
 */
import type {
  CapabilityReading,
  CapabilitySlot,
  DeviceCard,
  DeviceEvent,
  KeyMeasurementView,
  ProtocolErrorEvent,
} from "./ipc";

/** A message about a device that the user has to see and dismiss. */
export interface Notice extends ProtocolErrorEvent {
  /** Distinguishes two notices about the same device at different times. */
  seq: number;
}

export interface AppState {
  devices: Map<number, DeviceCard>;
  /**
   * Capability values that have been read, keyed by device and capability.
   *
   * Held here rather than in a component so that the rule below is one function
   * instead of a cleanup somebody forgets: **a value must not outlive the
   * session that read it.** A number left on screen after the device was
   * unplugged, or after its endpoint stalled, is the screen quietly reporting
   * hardware state that nothing currently supports — which is the failure this
   * ticket's acceptance criteria name.
   */
  readings: Map<string, CapabilityReading>;
  notices: Notice[];
  /** Monotonic, so a notice replacing another is visibly a different one. */
  nextSeq: number;
}

export function initialState(): AppState {
  return {
    devices: new Map(),
    readings: new Map(),
    notices: [],
    nextSeq: 1,
  };
}

function readingKey(deviceId: number, capability: string): string {
  return `${deviceId}:${capability}`;
}

export function withReading(
  state: AppState,
  deviceId: number,
  reading: CapabilityReading,
): AppState {
  const readings = new Map(state.readings);
  readings.set(readingKey(deviceId, reading.id), reading);
  return { ...state, readings };
}

export function readingFor(
  state: AppState,
  deviceId: number | null,
  capability: string,
): CapabilityReading | null {
  if (deviceId === null) return null;
  return state.readings.get(readingKey(deviceId, capability)) ?? null;
}

/** Drops every value read from one device. */
function forgetReadings(
  readings: Map<string, CapabilityReading>,
  deviceId: number,
): Map<string, CapabilityReading> {
  const kept = new Map(readings);
  for (const key of readings.keys()) {
    if (key.startsWith(`${deviceId}:`)) kept.delete(key);
  }
  return kept;
}

/**
 * Replaces the device list wholesale, from a `list_devices` answer.
 *
 * Wholesale rather than merged: the backend's list is the truth about what is
 * plugged in, and a merge would preserve a card for a device the backend has
 * stopped reporting — which is precisely the stale-screen failure this ticket
 * has to avoid.
 */
export function withDevices(state: AppState, cards: DeviceCard[]): AppState {
  return { ...state, devices: new Map(cards.map((card) => [card.id, card])) };
}

/**
 * Applies one backend event.
 *
 * Total over {@link DeviceEvent}: the `switch` has no default, so an event
 * added on the Rust side and forgotten here fails to compile rather than being
 * quietly dropped.
 */
export function applyEvent(state: AppState, event: DeviceEvent): AppState {
  switch (event.kind) {
    case "connected":
    case "updated": {
      const devices = new Map(state.devices);
      devices.set(event.card.id, event.card);
      // A card that is no longer in a session takes its values with it. This is
      // the disconnect, stall and vendor-conflict cases at once: whatever put
      // the device out of a session also ended the standing of anything read
      // through it.
      const readings = isConnected(event.card)
        ? state.readings
        : forgetReadings(state.readings, event.card.id);
      return { ...state, devices, readings };
    }
    case "disconnected": {
      const devices = new Map(state.devices);
      devices.delete(event.deviceId);
      // Notices about a device that has left go with it. A "reconnect the
      // cable" instruction for hardware that is no longer on the bus is an
      // instruction the user has already followed.
      const notices = state.notices.filter(
        (notice) => notice.deviceId !== event.deviceId,
      );
      return {
        ...state,
        devices,
        notices,
        readings: forgetReadings(state.readings, event.deviceId),
      };
    }
    case "protocolError": {
      const notice: Notice = { ...event.error, seq: state.nextSeq };
      // One notice per device: the newest replaces the previous, because a
      // stack of identical failures buries the instruction instead of
      // reinforcing it.
      const notices = state.notices
        .filter((existing) => existing.deviceId !== notice.deviceId)
        .concat(notice);
      return { ...state, notices, nextSeq: state.nextSeq + 1 };
    }
  }
}

export function dismissNotice(state: AppState, seq: number): AppState {
  return { ...state, notices: state.notices.filter((n) => n.seq !== seq) };
}

/** Devices in a stable order, so the list does not reshuffle between renders. */
export function deviceList(state: AppState): DeviceCard[] {
  return [...state.devices.values()].sort((a, b) => a.id - b.id);
}

export function noticeFor(
  state: AppState,
  deviceId: number | null,
): Notice | undefined {
  if (deviceId === null) return undefined;
  return state.notices.find((notice) => notice.deviceId === deviceId);
}

// --- the design rule, as functions -----------------------------------------

/** Whether a session is open, which is the only state a read can happen in. */
export function isConnected(card: DeviceCard): boolean {
  return card.connection.state === "connected";
}

/** Whether this device is quiet until it is physically reconnected. */
export function isStalled(card: DeviceCard): boolean {
  return card.connection.state === "stalled";
}

/** Whether connecting is something the user can be offered at all. */
export function canConnect(card: DeviceCard): boolean {
  return card.connection.state === "ready" || card.connection.state === "unreachable";
}

export function capabilitySlot(
  card: DeviceCard,
  id: string,
): CapabilitySlot | undefined {
  return card.capabilities.find((slot) => slot.id === id);
}

/**
 * Whether a capability may be read from this device right now.
 *
 * Two conditions, and both are needed. `readable` says a verified command
 * exists for this device's family; being connected says there is a session to
 * send it through. A button enabled on the first alone would be a control the
 * application cannot honour.
 */
export function canRead(card: DeviceCard, id: string): boolean {
  return (
    isConnected(card) && capabilitySlot(card, id)?.availability.state === "readable"
  );
}

/**
 * Whether any control that writes to this device may be enabled.
 *
 * Delegates to the backend's own answer rather than recomputing it. The two
 * halves — the family is verified, and a write command exists in this build's
 * ACL — are both facts the frontend has no way to establish, and a frontend
 * that decided this for itself would be a second place the rule lives.
 */
export function writeControlsEnabled(card: DeviceCard): boolean {
  return card.writes.enabled;
}

/**
 * Whether a reading may be presented as a fact about the device.
 *
 * The one thing the HE screen is allowed to gate a value's presentation on. A
 * value that did not come from hardware is still worth showing — it just must
 * not be shown the same way, because the difference between "we read this off
 * the board" and "software like this usually has one" is the difference between
 * a number and a guess.
 */
export function isFromHardware(reading: CapabilityReading): boolean {
  return reading.fromHardware;
}

// --- attaching values to a layout ------------------------------------------

/**
 * The per-key values from a reading, indexed by label.
 *
 * A label that appears more than once in the layout is dropped rather than
 * matched, and that is the interesting part. The capability layer identifies a
 * key by label on purpose — a vendor key id would need the vendor's key table to
 * render — but a layout has two keys called `Shift`, and attaching one value to
 * both would put a number under a key nobody measured. This project has already
 * spent one hardware exchange on two key-numbering spaces being treated as
 * interchangeable; showing a plausible number under the wrong key is the same
 * failure wearing a different hat.
 */
export function valuesByLabel(
  reading: CapabilityReading | null,
  layoutLabels: string[],
): Map<string, KeyMeasurementView> {
  const values = new Map<string, KeyMeasurementView>();
  if (reading === null || reading.value.shape !== "perKey") return values;

  const ambiguous = new Set(
    layoutLabels.filter(
      (label, index) => layoutLabels.indexOf(label) !== index,
    ),
  );
  for (const key of reading.value.keys) {
    if (ambiguous.has(key.label)) continue;
    values.set(key.label, key);
  }
  return values;
}
