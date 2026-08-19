/**
 * The rules a user would not notice being broken.
 *
 * Each test here corresponds to a way the screen could be quietly wrong: a
 * device that is gone still listed, a value that outlived its session, a control
 * enabled with nothing behind it, a number attributed to the wrong key. None of
 * those raise an error at runtime, and all of them look like a working
 * application.
 */
import { describe, expect, test } from "vitest";
import {
  applyEvent,
  canConnect,
  canRead,
  deviceList,
  dismissNotice,
  initialState,
  isConnected,
  isStalled,
  readingFor,
  valuesByLabel,
  withDevices,
  withReading,
  writeControlsEnabled,
} from "../model";
import { layoutLabels, PLACEHOLDER_75 } from "../layout";
import { actuationReading, card, connectedCard } from "./fixtures";

describe("the device list", () => {
  test("a device that goes away leaves the list", () => {
    // The acceptance criterion in so many words: unplugging must not leave the
    // screen sitting on the last thing it knew.
    let state = withDevices(initialState(), [card()]);
    expect(deviceList(state)).toHaveLength(1);

    state = applyEvent(state, { kind: "disconnected", deviceId: card().id });
    expect(deviceList(state)).toHaveLength(0);
  });

  test("an arrival and an update are the same card, not two", () => {
    let state = initialState();
    state = applyEvent(state, { kind: "connected", card: card() });
    state = applyEvent(state, { kind: "updated", card: connectedCard() });
    expect(deviceList(state)).toHaveLength(1);
    expect(isConnected(deviceList(state)[0]!)).toBe(true);
  });

  test("a fresh list replaces what was known rather than merging into it", () => {
    // A merge would keep a card the backend has stopped reporting, which is the
    // stale screen arriving by a different route.
    let state = withDevices(initialState(), [card(), card({ id: 2 })]);
    state = withDevices(state, [card()]);
    expect(deviceList(state).map((d) => d.id)).toEqual([card().id]);
  });

  test("the order does not depend on arrival order", () => {
    const a = card({ id: 10 });
    const b = card({ id: 2 });
    expect(deviceList(withDevices(initialState(), [a, b])).map((d) => d.id)).toEqual([
      2, 10,
    ]);
  });
});

describe("a value never outlives the session that read it", () => {
  test("disconnecting drops what was read from that device", () => {
    let state = withDevices(initialState(), [connectedCard()]);
    state = withReading(state, connectedCard().id, actuationReading());
    expect(readingFor(state, connectedCard().id, "he.actuation")).not.toBeNull();

    state = applyEvent(state, {
      kind: "updated",
      card: card({ connection: { state: "ready" } }),
    });
    expect(readingFor(state, card().id, "he.actuation")).toBeNull();
  });

  test("a stall drops it too", () => {
    // The dangerous case. A stalled endpoint means the device is quiet until it
    // is physically reconnected, so anything on screen from before is a
    // measurement of a state nothing can currently confirm.
    let state = withReading(
      withDevices(initialState(), [connectedCard()]),
      connectedCard().id,
      actuationReading(),
    );
    state = applyEvent(state, {
      kind: "updated",
      card: card({
        connection: { state: "stalled", userMessage: "unplug it, wait ten seconds" },
      }),
    });
    expect(readingFor(state, card().id, "he.actuation")).toBeNull();
    expect(isStalled(deviceList(state)[0]!)).toBe(true);
  });

  test("unplugging drops it", () => {
    let state = withReading(
      withDevices(initialState(), [connectedCard()]),
      connectedCard().id,
      actuationReading(),
    );
    state = applyEvent(state, { kind: "disconnected", deviceId: card().id });
    expect(readingFor(state, card().id, "he.actuation")).toBeNull();
  });

  test("a value survives an update that keeps the session open", () => {
    // The other direction, so the rule above is not satisfied by dropping
    // everything all the time.
    let state = withReading(
      withDevices(initialState(), [connectedCard()]),
      connectedCard().id,
      actuationReading(),
    );
    state = applyEvent(state, { kind: "updated", card: connectedCard() });
    expect(readingFor(state, card().id, "he.actuation")).not.toBeNull();
  });
});

describe("no control without a confirmed command behind it", () => {
  test("a capability with no verified command is not readable", () => {
    expect(canRead(card(), "he.actuation")).toBe(false);
  });

  test("a readable capability still needs a session", () => {
    // `readable` says a verified command exists for this family. It does not say
    // there is anything open to send it through.
    const readableButNotConnected = card({
      capabilities: [{ id: "he.actuation", availability: { state: "readable" } }],
      connection: { state: "ready" },
    });
    expect(canRead(readableButNotConnected, "he.actuation")).toBe(false);
    expect(canRead(connectedCard(), "he.actuation")).toBe(true);
  });

  test("a capability this frontend has never heard of is simply not readable", () => {
    expect(canRead(connectedCard(), "he.rt.up")).toBe(false);
  });

  test("a verified family alone does not enable a write control", () => {
    // Both halves are required and today the second is always zero: the family
    // reaches `verified` and the build still contains no write command. A UI
    // that keyed off the family alone would offer to write on a build that
    // cannot.
    const verifiedFamily = connectedCard();
    expect(verifiedFamily.identity.family.permitsWrite).toBe(true);
    expect(verifiedFamily.writes.commandsAvailable).toBe(0);
    expect(writeControlsEnabled(verifiedFamily)).toBe(false);
  });

  test("write controls turn on only when both halves say so", () => {
    const hypothetical = connectedCard({
      writes: { familyPermits: true, commandsAvailable: 1, enabled: true },
    });
    expect(writeControlsEnabled(hypothetical)).toBe(true);
  });

  test("a stalled device is not offered a connect", () => {
    // Reopening does not clear a stalled endpoint and more traffic keeps it
    // pinned, so the button must not be there to press.
    const stalled = card({
      connection: { state: "stalled", userMessage: "unplug it" },
    });
    expect(canConnect(stalled)).toBe(false);
  });

  test("a device with no configuration channel is not offered a connect", () => {
    expect(canConnect(card({ connection: { state: "noConfigEndpoint" } }))).toBe(
      false,
    );
  });

  test("a channel that would not open may be tried again", () => {
    // Distinct from a stall on purpose: the usual cause is the vendor's software
    // holding the endpoint, which the user can close.
    const busy = card({
      connection: {
        state: "unreachable",
        userMessage: "the configuration channel would not open",
        vendorSoftwareSuspected: true,
      },
    });
    expect(canConnect(busy)).toBe(true);
  });
});

describe("notices", () => {
  test("a second failure on one device replaces the first", () => {
    let state = initialState();
    for (const message of ["first", "second"]) {
      state = applyEvent(state, {
        kind: "protocolError",
        error: { deviceId: 1, userMessage: message, recoverable: false },
      });
    }
    expect(state.notices).toHaveLength(1);
    expect(state.notices[0]!.userMessage).toBe("second");
  });

  test("failures on different devices both stand", () => {
    let state = initialState();
    for (const deviceId of [1, 2]) {
      state = applyEvent(state, {
        kind: "protocolError",
        error: { deviceId, userMessage: "stalled", recoverable: false },
      });
    }
    expect(state.notices).toHaveLength(2);
  });

  test("a notice goes away with the device it is about", () => {
    // "Reconnect the cable" for hardware that is no longer on the bus is an
    // instruction the user has already followed.
    let state = applyEvent(initialState(), {
      kind: "protocolError",
      error: { deviceId: 1, userMessage: "stalled", recoverable: false },
    });
    state = applyEvent(state, { kind: "disconnected", deviceId: 1 });
    expect(state.notices).toHaveLength(0);
  });

  test("a dismissed notice stays dismissed", () => {
    let state = applyEvent(initialState(), {
      kind: "protocolError",
      error: { deviceId: 1, userMessage: "stalled", recoverable: false },
    });
    state = dismissNotice(state, state.notices[0]!.seq);
    expect(state.notices).toHaveLength(0);
  });
});

describe("attaching values to keys", () => {
  const labels = layoutLabels(PLACEHOLDER_75);

  test("the four keys that were read land on the four keys of those names", () => {
    const values = valuesByLabel(actuationReading(), labels);
    expect(values.get("W")?.measurement.rendered).toBe("0.51 mm");
    expect(values.get("A")?.measurement.rendered).toBe("1.02 mm");
    expect(values.get("S")?.measurement.rendered).toBe("1.49 mm");
    expect(values.get("D")?.measurement.rendered).toBe("2.00 mm");
  });

  test("no other key gets a value", () => {
    const values = valuesByLabel(actuationReading(), labels);
    expect(values.size).toBe(4);
    expect(values.has("F")).toBe(false);
  });

  test("a label the layout draws twice gets no value at all", () => {
    // The layout has two keys called Shift. Attaching one measurement to both
    // would print a number under a key nobody measured — a plausible wrong
    // answer, which is the class of bug this project has already paid for once
    // by confusing two key-numbering spaces.
    expect(labels.filter((l) => l === "Shift")).toHaveLength(2);
    const values = valuesByLabel(
      actuationReading({
        value: {
          shape: "perKey",
          keys: [
            {
              label: "Shift",
              measurement: { value: 1, unit: "mm", decimals: 2, rendered: "1.00 mm" },
            },
          ],
        },
      }),
      labels,
    );
    expect(values.has("Shift")).toBe(false);
  });

  test("nothing read means nothing shown", () => {
    expect(valuesByLabel(null, labels).size).toBe(0);
  });

  test("a stream-shaped capability puts nothing on the keyboard", () => {
    // A stream carries no samples by design. Drawing a keyboard from one would
    // be inventing a snapshot of something that does not have snapshots.
    const values = valuesByLabel(
      actuationReading({ value: { shape: "stream", unit: "mm" } }),
      labels,
    );
    expect(values.size).toBe(0);
  });
});
