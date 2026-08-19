/**
 * The IPC contract, against a mocked `pcore`.
 *
 * What these check is the half that a type system cannot: the *strings*. A
 * command name and an argument name are text on both sides of the boundary, and
 * getting either wrong produces no build error — just a promise that rejects
 * into an empty screen, or a listener that never fires. The Rust side has the
 * mirror of these tests (`app/src-tauri/src/ipc/mod.rs`), which reads this
 * project's own `ipc.ts` and refuses names it does not handle.
 */
import { beforeEach, describe, expect, test, vi } from "vitest";

const invoke = vi.fn();
const listen = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({
  invoke,
  // A stand-in for Tauri's Channel: enough of one to prove the analog stream is
  // subscribed through a channel rather than through the event bus.
  Channel: class {
    onmessage: ((sample: unknown) => void) | null = null;
  },
}));

vi.mock("@tauri-apps/api/event", () => ({ listen }));

const {
  buildId,
  connectDevice,
  disconnectDevice,
  journal,
  listDevices,
  onBatteryChanged,
  onDeviceEvents,
  readCapability,
  subscribeAnalogStream,
  EVENTS,
} = await import("../ipc");

beforeEach(() => {
  invoke.mockReset();
  listen.mockReset();
  invoke.mockResolvedValue([]);
  listen.mockResolvedValue(() => {});
});

describe("commands", () => {
  test("each one sends the name the backend registers", async () => {
    await buildId();
    await listDevices();
    await journal();
    expect(invoke.mock.calls.map(([name]) => name)).toEqual([
      "build_id",
      "list_devices",
      "journal",
    ]);
  });

  test("connect and disconnect carry the device id under the name Rust expects", async () => {
    // Argument names are text too. A rename from `id` to `deviceId` on one side
    // produces a command that is invoked and refuses to deserialise, which
    // surfaces as an error message about a missing field and nothing else.
    await connectDevice(0x372e103e);
    await disconnectDevice(0x372e103e);
    expect(invoke).toHaveBeenNthCalledWith(1, "connect_device", { id: 0x372e103e });
    expect(invoke).toHaveBeenNthCalledWith(2, "disconnect_device", {
      id: 0x372e103e,
    });
  });

  test("a capability is asked for by its canonical id", async () => {
    await readCapability(7, "he.actuation");
    expect(invoke).toHaveBeenCalledWith("read_capability", {
      id: 7,
      capability: "he.actuation",
    });
  });

  test("a failure from the backend reaches the caller rather than being swallowed", async () => {
    // Everything on screen that can fail says why. A command layer that turned a
    // rejection into an empty result would produce a screen that looks like a
    // device with nothing on it.
    invoke.mockRejectedValueOnce("the configuration channel would not open");
    await expect(listDevices()).rejects.toBe(
      "the configuration channel would not open",
    );
  });
});

describe("events", () => {
  test("the names are the ones the backend emits", () => {
    expect(Object.values(EVENTS)).toEqual([
      "device:connected",
      "device:updated",
      "device:disconnected",
      "device:battery-changed",
      "device:protocol-error",
    ]);
  });

  test("subscribing takes all four device events at once", async () => {
    await onDeviceEvents(() => {});
    expect(listen.mock.calls.map(([name]) => name)).toEqual([
      EVENTS.deviceConnected,
      EVENTS.deviceUpdated,
      EVENTS.deviceDisconnected,
      EVENTS.protocolError,
    ]);
  });

  test("unsubscribing undoes all of them", async () => {
    // Four listeners undone by one call. A component that released three of them
    // would keep mutating state nothing is rendering.
    const offs = [vi.fn(), vi.fn(), vi.fn(), vi.fn()];
    let next = 0;
    listen.mockImplementation(() => Promise.resolve(offs[next++]!));

    const unlisten = await onDeviceEvents(() => {});
    unlisten();
    for (const off of offs) expect(off).toHaveBeenCalledOnce();
  });

  test("each event arrives tagged with what kind it is", async () => {
    const handlers: Record<string, (event: { payload: unknown }) => void> = {};
    listen.mockImplementation((name: string, handler: (e: { payload: unknown }) => void) => {
      handlers[name] = handler;
      return Promise.resolve(() => {});
    });

    const seen: unknown[] = [];
    await onDeviceEvents((event) => seen.push(event));

    handlers[EVENTS.deviceConnected]!({ payload: { id: 1 } });
    handlers[EVENTS.deviceDisconnected]!({ payload: { deviceId: 1 } });
    handlers[EVENTS.protocolError]!({
      payload: { deviceId: 1, userMessage: "stalled", recoverable: false },
    });

    expect(seen).toEqual([
      { kind: "connected", card: { id: 1 } },
      { kind: "disconnected", deviceId: 1 },
      {
        kind: "protocolError",
        error: { deviceId: 1, userMessage: "stalled", recoverable: false },
      },
    ]);
  });

  test("battery is declared and nothing emits it yet", async () => {
    // Declared so TICKET-19/20 write against this shape; unemitted because no
    // device this project speaks to reports a charge, and a number invented to
    // fill the field would be worse than a quiet one.
    await onBatteryChanged(() => {});
    expect(listen).toHaveBeenCalledWith(EVENTS.batteryChanged, expect.any(Function));
  });
});

describe("the analog stream", () => {
  test("is reserved, and refuses rather than being absent", async () => {
    // The contract exists now so TICKET-15 is written against it from its first
    // line. What must not happen is the stream quietly appearing on the general
    // event bus later, because retrofitting an ordered channel onto code already
    // wired that way is a breaking change rather than an addition.
    invoke.mockRejectedValueOnce("analog stream is not implemented yet (TICKET-15)");
    await expect(subscribeAnalogStream(1, () => {})).rejects.toContain(
      "TICKET-15",
    );
  });

  test("hands the backend a channel, not a listener", async () => {
    invoke.mockResolvedValueOnce(undefined);
    await subscribeAnalogStream(1, () => {});
    const [name, args] = invoke.mock.calls[0]!;
    expect(name).toBe("subscribe_analog_stream");
    expect(args).toMatchObject({ deviceId: 1 });
    expect(args.channel).toBeDefined();
    expect(listen).not.toHaveBeenCalled();
  });
});
