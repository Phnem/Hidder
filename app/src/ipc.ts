/**
 * The frontend half of the IPC contract. See app/src-tauri/src/ipc/mod.rs for
 * why there are three mechanisms and which traffic belongs in each.
 *
 * Everything crossing the boundary is declared here rather than at call sites,
 * so a rename on the Rust side breaks one file instead of leaking through the
 * component tree as `invoke("some_string")`. A test on the Rust side reads this
 * file and fails if a name here is not one it handles, and vice versa — a
 * mismatch is otherwise silent in both directions.
 *
 * The types below mirror `pcore::model`. They are hand-written rather than
 * generated, and the discipline that keeps them honest is that every state the
 * Rust side keeps separate stays separate here: no `string | null` where the
 * other side has three variants, because collapsing them is how "this board
 * lacks the feature" and "we have no way to ask" become the same grey box.
 */
import { Channel, invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// --- the vocabulary --------------------------------------------------------

/** How sure one axis of identity is. Never a device's overall confidence. */
export type Confidence = "unknown" | "candidate" | "high" | "verified";

/** Where knowledge about a capability came from. */
export type OriginLabel =
  | "verified on hardware"
  | "from a vendor artifact"
  | "assumed";

export interface SignalView {
  signal: string;
  /** `differed` argues against a match; `absent` argues nothing at all. */
  state: "matched" | "differed" | "absent";
}

export interface AxisView {
  answer: string | null;
  confidence: Confidence;
  signals: SignalView[];
}

export interface FamilyView {
  family: string | null;
  confidence: Confidence;
  reason:
    | "no_evidence"
    | "not_recorded"
    | "from_registry"
    | "from_protocol_evidence";
  /** One of two conditions for a write. See {@link WriteView}. */
  permitsWrite: boolean;
  signals: SignalView[];
}

export interface IdentityView {
  structural: AxisView;
  product: AxisView;
  family: FamilyView;
}

/** Where a device stands between "plugged in" and "we are talking to it". */
export type ConnectionView =
  | { state: "noConfigEndpoint" }
  | { state: "ready" }
  | { state: "connected" }
  | {
      state: "unreachable";
      userMessage: string;
      /** A suspicion, never a finding: the backend gives no error code. */
      vendorSoftwareSuspected: boolean;
    }
  | { state: "stalled"; userMessage: string };

/**
 * Why a capability has no value.
 *
 * Four states rather than a boolean, and the UI must render them differently:
 * only one of them can ever change by installing an update.
 */
export type AvailabilityView =
  | { state: "readable" }
  | { state: "notRead" }
  | { state: "noVerifiedCommand" }
  | { state: "notPresent"; evidence: string };

export interface CapabilitySlot {
  /** The canonical id, e.g. `he.actuation`. */
  id: string;
  availability: AvailabilityView;
}

export interface WriteView {
  familyPermits: boolean;
  commandsAvailable: number;
  /** Both conditions. The only field a write control may be enabled on. */
  enabled: boolean;
}

export interface DeviceCard {
  id: number;
  hardwareId: string;
  label: string;
  product: string | null;
  manufacturer: string | null;
  /** Whether one exists — never its value. */
  serialPresent: boolean;
  identity: IdentityView;
  connection: ConnectionView;
  capabilities: CapabilitySlot[];
  writes: WriteView;
  modelId: number | null;
}

export interface MeasurementView {
  value: number;
  unit: string;
  decimals: number;
  /**
   * How the value should be shown. Use this rather than formatting `value`:
   * how many decimals mean anything is a fact about the device's own step.
   */
  rendered: string;
}

export interface KeyMeasurementView {
  label: string;
  measurement: MeasurementView;
}

export type ValueView =
  | { shape: "scalar"; measurement: MeasurementView }
  | { shape: "perKey"; keys: KeyMeasurementView[] }
  | { shape: "compound"; entries: KeyMeasurementView[] }
  | { shape: "stream"; unit: string };

export interface CapabilityReading {
  id: string;
  origin: OriginLabel;
  originCommand: string | null;
  /** Whether this may be presented as a fact rather than as an expectation. */
  fromHardware: boolean;
  confidence: Confidence;
  provenance: string;
  value: ValueView;
  readAtUnixMs: number;
}

export interface JournalRow {
  atUnixMs: number;
  atMs: number;
  deviceId: number;
  family: string;
  command: string;
  class: string;
  intent: "read" | "probe" | "write";
  payloadLen: number;
  outcome: "ok" | "refused" | "failed" | "rejected";
  detail: string | null;
  verification: string;
  line: string;
}

// --- Mechanism 1: commands (request/response, UI-initiated) -----------------

export function buildId(): Promise<string> {
  return invoke<string>("build_id");
}

export function listDevices(): Promise<DeviceCard[]> {
  return invoke<DeviceCard[]>("list_devices");
}

/**
 * Opens a session: sends the device one command and establishes its protocol
 * family from the answer.
 *
 * Called because a person asked, never on device arrival. The vendor's own
 * software may be holding the configuration endpoint, and the policy is to
 * detect that and say so rather than race for it.
 */
export function connectDevice(id: number): Promise<DeviceCard> {
  return invoke<DeviceCard>("connect_device", { id });
}

export function disconnectDevice(id: number): Promise<DeviceCard> {
  return invoke<DeviceCard>("disconnect_device", { id });
}

/** Reads one capability by its canonical id. */
export function readCapability(
  id: number,
  capability: string,
): Promise<CapabilityReading> {
  return invoke<CapabilityReading>("read_capability", { id, capability });
}

/** Everything the safety gate authorised in this run, oldest first. */
export function journal(): Promise<JournalRow[]> {
  return invoke<JournalRow[]>("journal");
}

// --- Vetro Probe research flow (thin bridge to the Python engine sidecar) ----

/** Device discovery state (no protocol knowledge leaks into the UI). */
export type ProbeDiscoveryState =
  | "NO_DEVICE"
  | "DISCOVERING"
  | "IDENTIFIED"
  | "UNSUPPORTED"
  | "IDENTITY_MISMATCH"
  | "FW_UNSUPPORTED"
  | "ERROR";

export interface ProbeDeviceView {
  name: string | null;
  firmware: string | null;
  family: string;
}

export interface ProbeDiscovery {
  state: ProbeDiscoveryState;
  device: ProbeDeviceView | null;
  supportedCount: number;
  reason: string;
}

export interface ProbeOperation {
  id: string;
  label: string;
  classification: "AUTO_REVERSIBLE" | "BLOCKED" | string;
  whySafe?: string;
}

export interface ProbePlan {
  safe: ProbeOperation[];
  blocked: ProbeOperation[];
  safeCount: number;
}

export interface ProbeRecoveryStatus {
  preflight: "CLEAR" | "RECOVERING" | "RECOVERED" | "BLOCKED" | string;
  pending: boolean;
  reason: string;
}

export interface ProbeRunResult {
  status:
    | "COMPLETE_PASS"
    | "SUCCESS_RESTORED"
    | "FAIL_RESTORED"
    | "FAILED_REQUIRES_MANUAL_RESTORE"
    | "RECOVERY_IN_PROGRESS"
    | string;
  restored: boolean;
  checksCompleted?: number;
  checksTotal?: number;
  checks_completed?: number;
  checks_total?: number;
  results: { id: string; label?: string; status: string; restored: boolean }[];
  evidenceSource?: string;
  evidence_source?: string;
  physicalValidationEvidence?: boolean;
  physical_validation_evidence?: boolean;
  outputPath?: string | null;
  output_path?: string | null;
  error?: string;
}

export type ProbeProgressState =
  | "QUEUED"
  | "BASELINING"
  | "TESTING"
  | "VERIFYING"
  | "RESTORING"
  | "PASS"
  | "BLOCKED"
  | "FAILED"
  | "RECOVERING";

export interface ProbeProgress {
  op: string;
  label?: string;
  state: ProbeProgressState;
  text?: string;
}

export function probeDiscover(): Promise<ProbeDiscovery> {
  return invoke<ProbeDiscovery>("probe_discover");
}

export function probePlan(): Promise<ProbePlan> {
  return invoke<ProbePlan>("probe_plan");
}

export function probeRecoveryStatus(): Promise<ProbeRecoveryStatus> {
  return invoke<ProbeRecoveryStatus>("probe_recovery_status");
}

export function probeStartRun(): Promise<{ started: boolean; error?: string }> {
  return invoke<{ started: boolean; error?: string }>("probe_start_run");
}

export function probeRunResult(): Promise<ProbeRunResult> {
  return invoke<ProbeRunResult>("probe_run_result");
}

export function probeClearRecovery(): Promise<ProbeRecoveryStatus> {
  return invoke<ProbeRecoveryStatus>("probe_clear_recovery");
}

export function probeSetMode(mode: "demo" | "real", scenario: string): Promise<{ ok: boolean }> {
  return invoke<{ ok: boolean }>("probe_set_mode", { mode, scenario });
}

export function probeOpenResults(): Promise<void> {
  return invoke<void>("probe_open_results");
}

/** Event names must match app/src-tauri/src/ipc/events.rs exactly. */
export const PROBE_EVENTS = {
  progress: "probe:progress",
  runResult: "probe:run_result",
} as const;

export type ProbeEngineEvent =
  | { kind: "progress"; progress: ProbeProgress }
  | { kind: "runResult"; result: ProbeRunResult };

/** Subscribes to both Probe engine events as one stream. */
export async function onProbeEvents(
  handler: (event: ProbeEngineEvent) => void,
): Promise<UnlistenFn> {
  const unlisteners = await Promise.all([
    listen<ProbeProgress>(PROBE_EVENTS.progress, (e) =>
      handler({ kind: "progress", progress: e.payload }),
    ),
    listen<ProbeRunResult>(PROBE_EVENTS.runResult, (e) =>
      handler({ kind: "runResult", result: e.payload }),
    ),
  ]);
  return () => unlisteners.forEach((off) => off());
}

// --- Mechanism 2: events (rare, backend-initiated) -------------------------

/** Event names must match app/src-tauri/src/ipc/events.rs exactly. */
export const EVENTS = {
  deviceConnected: "device:connected",
  deviceUpdated: "device:updated",
  deviceDisconnected: "device:disconnected",
  batteryChanged: "device:battery-changed",
  protocolError: "device:protocol-error",
} as const;

export interface DeviceGoneEvent {
  deviceId: number;
}

export interface ProtocolErrorEvent {
  deviceId: number;
  /** Written for the person holding the keyboard, not for a log. */
  userMessage: string;
  /** False for a stalled endpoint: there is nothing to retry, so offer nothing. */
  recoverable: boolean;
}

export interface BatteryEvent {
  deviceId: number;
  percent: number;
  charging: boolean;
}

/**
 * Everything the backend can announce, in one union.
 *
 * Declared as one type so the state module can be a total function over it: a
 * new event added on the Rust side and forgotten here becomes a type error at
 * the place that handles them, rather than a message silently ignored.
 */
export type DeviceEvent =
  | { kind: "connected"; card: DeviceCard }
  | { kind: "updated"; card: DeviceCard }
  | { kind: "disconnected"; deviceId: number }
  | { kind: "protocolError"; error: ProtocolErrorEvent };

/**
 * Subscribes to every device event and hands them over as one stream.
 *
 * One subscription point rather than four, because the four have to be undone
 * together: a component that unlistened three of them would keep receiving the
 * fourth and mutate state nobody is rendering.
 */
export async function onDeviceEvents(
  handler: (event: DeviceEvent) => void,
): Promise<UnlistenFn> {
  const unlisteners = await Promise.all([
    listen<DeviceCard>(EVENTS.deviceConnected, (e) =>
      handler({ kind: "connected", card: e.payload }),
    ),
    listen<DeviceCard>(EVENTS.deviceUpdated, (e) =>
      handler({ kind: "updated", card: e.payload }),
    ),
    listen<DeviceGoneEvent>(EVENTS.deviceDisconnected, (e) =>
      handler({ kind: "disconnected", deviceId: e.payload.deviceId }),
    ),
    listen<ProtocolErrorEvent>(EVENTS.protocolError, (e) =>
      handler({ kind: "protocolError", error: e.payload }),
    ),
  ]);
  return () => unlisteners.forEach((off) => off());
}

/**
 * Charge changes for a wireless device.
 *
 * Declared, and nothing emits it yet: no device this project speaks to reports
 * a charge. TICKET-19/20.
 */
export function onBatteryChanged(
  handler: (event: BatteryEvent) => void,
): Promise<UnlistenFn> {
  return listen<BatteryEvent>(EVENTS.batteryChanged, (e) => handler(e.payload));
}

// --- Mechanism 3: channels (ordered, high-throughput) ----------------------

export interface AnalogSample {
  seq: number;
  key: number;
  /** Normalised travel, 0..1. Never raw sensor units. */
  value: number;
}

/**
 * Subscribe to the analog travel stream.
 *
 * A channel, not an event listener: async event listeners may run out of order,
 * and a reordered waveform is wrong data rather than late data — while looking
 * on screen exactly like a real measurement. Rejects until TICKET-15 implements
 * it; the call exists now so that ticket is written against this shape.
 */
export async function subscribeAnalogStream(
  deviceId: number,
  onSample: (sample: AnalogSample) => void,
): Promise<void> {
  const channel = new Channel<AnalogSample>();
  channel.onmessage = onSample;
  await invoke("subscribe_analog_stream", { deviceId, channel });
}
