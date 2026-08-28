/**
 * The window: navigation, the one place backend calls are made, and the status
 * bar.
 *
 * # Where the traffic comes from
 *
 * Every device operation in this file is the direct result of something a person
 * did — pressing Connect, pressing Read, opening the Journal. There is no
 * interval anywhere and there must never be one: polling from a front-end
 * competes with real work on the same endpoint and adds to the write pressure
 * that stalls it, and prior art had to introduce a liveness cache specifically
 * to stop its own frontend's polling from reaching the wire.
 *
 * Device *state* arrives the other way, as events from `pcore`, which watches by
 * asking the operating system rather than by asking any hardware. That is the
 * whole reason FR11 has two mechanisms.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  buildId,
  connectDevice,
  disconnectDevice,
  journal as fetchJournal,
  listDevices,
  onDeviceEvents,
  readCapability,
  type JournalRow,
} from "./ipc";
import {
  applyEvent,
  deviceList,
  dismissNotice,
  initialState,
  isConnected,
  readingFor,
  withDevices,
  withReading,
} from "./model";
import { DevicesScreen } from "./screens/Devices";
import { ACTUATION, HeScreen } from "./screens/He";
import { JournalScreen } from "./screens/Journal";
import { AboutScreen } from "./screens/About";
import { ResearchScreen } from "./screens/Research";
import { connectionSummary } from "./components";

/**
 * The screens the specification lists, and which of them exist.
 *
 * The unbuilt ones stay visible and disabled rather than being hidden, so the
 * shape of the finished product is legible from the first build — and so that a
 * screen appearing is a change somebody notices.
 */
const SCREENS = [
  { id: "research", label: "Research", ready: true },
  { id: "devices", label: "Devices", ready: true },
  { id: "he", label: "HE", ready: true },
  { id: "analog", label: "Analog Monitor", ready: false, ticket: "TICKET-15" },
  { id: "profiles", label: "Profiles", ready: false, ticket: "TICKET-17" },
  { id: "keys", label: "Keys", ready: false, ticket: "later" },
  { id: "lighting", label: "Lighting", ready: false, ticket: "later" },
  { id: "macros", label: "Macros", ready: false, ticket: "later" },
  { id: "learning", label: "Learning Mode", ready: false, ticket: "TICKET-16" },
  { id: "journal", label: "Journal", ready: true },
  { id: "about", label: "About", ready: true },
] as const;

type ScreenId = (typeof SCREENS)[number]["id"];

export function App() {
  const [state, setState] = useState(initialState);
  const [screen, setScreen] = useState<ScreenId>("devices");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [rows, setRows] = useState<JournalRow[]>([]);
  const [version, setVersion] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;

    void (async () => {
      // Subscribed before the first list is asked for. The other order has a
      // window in which a device arrives, its event is missed, and the screen is
      // wrong until something else happens to refresh it.
      const off = await onDeviceEvents((event) =>
        setState((previous) => applyEvent(previous, event)),
      );
      if (cancelled) {
        off();
        return;
      }
      unlisten = off;

      try {
        const cards = await listDevices();
        setState((previous) => withDevices(previous, cards));
      } catch (cause) {
        setFailure(messageOf(cause));
      }
    })();

    buildId().then(setVersion).catch(() => setVersion(""));

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  const devices = useMemo(() => deviceList(state), [state]);

  // Pick something as soon as there is something to pick, so the HE screen is
  // not empty for a device that is plainly the only one plugged in.
  useEffect(() => {
    if (selectedId !== null && state.devices.has(selectedId)) return;
    const next = devices.find((device) => device.connection.state !== "noConfigEndpoint");
    setSelectedId(next?.id ?? devices[0]?.id ?? null);
  }, [devices, selectedId, state.devices]);

  const selected = selectedId === null ? null : (state.devices.get(selectedId) ?? null);
  const reading = readingFor(state, selectedId, ACTUATION);

  const connect = useCallback(async (id: number) => {
    setBusyId(id);
    setFailure(null);
    try {
      const card = await connectDevice(id);
      // The backend also emits an update for this. Applying the answer directly
      // means the button stops saying "Connecting…" the moment the call returns,
      // rather than one event later; the event that follows carries the same
      // card, so applying both is idempotent.
      setState((previous) => applyEvent(previous, { kind: "updated", card }));
    } catch (cause) {
      setFailure(messageOf(cause));
    } finally {
      setBusyId(null);
    }
  }, []);

  const disconnect = useCallback(async (id: number) => {
    setBusyId(id);
    try {
      const card = await disconnectDevice(id);
      setState((previous) => applyEvent(previous, { kind: "updated", card }));
    } catch (cause) {
      setFailure(messageOf(cause));
    } finally {
      setBusyId(null);
    }
  }, []);

  const read = useCallback(async () => {
    if (selectedId === null) return;
    setBusy(true);
    setFailure(null);
    try {
      const value = await readCapability(selectedId, ACTUATION);
      setState((previous) => withReading(previous, selectedId, value));
    } catch (cause) {
      setFailure(messageOf(cause));
    } finally {
      setBusy(false);
    }
  }, [selectedId]);

  const refreshJournal = useCallback(async () => {
    setBusy(true);
    try {
      setRows(await fetchJournal());
    } catch (cause) {
      setFailure(messageOf(cause));
    } finally {
      setBusy(false);
    }
  }, []);

  // The journal is fetched when its screen is opened, and at no other time. It
  // is a question to `pcore`'s own record, not to a device, but a table nobody
  // is looking at is still work nobody asked for.
  useEffect(() => {
    if (screen === "journal") void refreshJournal();
  }, [screen, refreshJournal]);

  return (
    <div className="shell">
      <header>
        <h1>Peripheral</h1>
        <p className="build mono">{version}</p>
      </header>

      <nav aria-label="Screens">
        <ul>
          {SCREENS.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                disabled={!entry.ready}
                aria-current={screen === entry.id ? "page" : undefined}
                title={entry.ready ? undefined : `Not built yet (${entry.ticket})`}
                onClick={() => setScreen(entry.id)}
              >
                {entry.label}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <main>
        {state.notices.map((notice) => (
          <div className="notice" key={notice.seq} role="alert">
            <div>
              <p>{notice.userMessage}</p>
              {!notice.recoverable && (
                <p className="muted">
                  There is nothing to retry. Further attempts keep the endpoint in
                  this state.
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() =>
                setState((previous) => dismissNotice(previous, notice.seq))
              }
            >
              Dismiss
            </button>
          </div>
        ))}

        {failure !== null && (
          <div className="notice" role="alert">
            <p>{failure}</p>
            <button type="button" onClick={() => setFailure(null)}>
              Dismiss
            </button>
          </div>
        )}

        {screen === "research" && <ResearchScreen />}

        {screen === "devices" && (
          <DevicesScreen
            devices={devices}
            selectedId={selectedId}
            busyId={busyId}
            onSelect={setSelectedId}
            onConnect={(id) => void connect(id)}
            onDisconnect={(id) => void disconnect(id)}
          />
        )}

        {screen === "he" && (
          <HeScreen
            device={selected}
            reading={reading}
            busy={busy}
            selectedKey={selectedKey}
            onSelectKey={setSelectedKey}
            onRead={() => void read()}
          />
        )}

        {screen === "journal" && (
          <JournalScreen rows={rows} onRefresh={() => void refreshJournal()} busy={busy} />
        )}

        {screen === "about" && <AboutScreen version={version} devices={devices} />}
      </main>

      <footer className="statusbar">
        <span>
          {devices.length === 0
            ? "no devices"
            : `${devices.length} device${devices.length === 1 ? "" : "s"}`}
        </span>
        {selected !== null && (
          <>
            <span>·</span>
            <span>{selected.label}</span>
            <span>·</span>
            <span>{connectionSummary(selected.connection).label}</span>
          </>
        )}
        <span className="spacer" />
        <span>
          {devices.some(isConnected) ? "session open" : "read-only, no session"}
        </span>
      </footer>
    </div>
  );
}

function messageOf(cause: unknown): string {
  if (typeof cause === "string") return cause;
  if (cause instanceof Error) return cause.message;
  return String(cause);
}
