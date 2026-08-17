/**
 * The frontend half of the IPC contract. See app/src-tauri/src/ipc/mod.rs for
 * why there are three mechanisms and which traffic belongs in each.
 *
 * Everything crossing the boundary is declared here rather than at call sites,
 * so a rename on the Rust side breaks one file instead of leaking through the
 * component tree as `invoke("some_string")`.
 */
import { invoke } from "@tauri-apps/api/core";
import { Channel } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// --- Mechanism 1: commands (request/response, UI-initiated) -----------------

export interface DeviceView {
  id: number;
  label: string;
  /** A device whose protocol family is not verified opens read-only. */
  readOnly: boolean;
}

export function buildId(): Promise<string> {
  return invoke<string>("build_id");
}

export function listDevices(): Promise<DeviceView[]> {
  return invoke<DeviceView[]>("list_devices");
}

// --- Mechanism 2: events (rare, backend-initiated) -------------------------

/** Event names must match app/src-tauri/src/ipc/events.rs exactly. */
export const EVENTS = {
  deviceConnected: "device:connected",
  deviceDisconnected: "device:disconnected",
  batteryChanged: "device:battery-changed",
  protocolError: "device:protocol-error",
} as const;

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

export function onProtocolError(
  handler: (event: ProtocolErrorEvent) => void,
): Promise<UnlistenFn> {
  return listen<ProtocolErrorEvent>(EVENTS.protocolError, (e) =>
    handler(e.payload),
  );
}

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
 * and a reordered waveform is wrong data rather than late data. Rejects until
 * TICKET-15 implements it.
 */
export async function subscribeAnalogStream(
  deviceId: number,
  onSample: (sample: AnalogSample) => void,
): Promise<void> {
  const channel = new Channel<AnalogSample>();
  channel.onmessage = onSample;
  await invoke("subscribe_analog_stream", { deviceId, channel });
}
