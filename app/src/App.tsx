import { useEffect, useState } from "react";
import { listDevices, buildId, type DeviceView } from "./ipc";

/// Screen stubs, in the order the specification lists them. Nothing is
/// implemented here: TICKET-13 builds the real screens.
///
/// The design rule that governs all of them, from the first commit: never show a
/// control that has no confirmed command behind it. An unconfirmed capability is
/// either hidden or shown greyed out and labelled as unconfirmed on this model.
/// It is not shown as working.
const SCREENS = [
  "Devices",
  "HE",
  "Analog Monitor",
  "Profiles",
  "Keys",
  "Lighting",
  "Macros",
  "Learning Mode",
  "Journal",
  "About",
] as const;

export function App() {
  const [devices, setDevices] = useState<DeviceView[] | null>(null);
  const [version, setVersion] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // One call each, on mount. Deliberately not an interval: the UI never
    // reaches a device to refresh an indicator, because polling competes with
    // real work on the same endpoint. Device state arrives by event once
    // TICKET-12 wires pcore up.
    buildId().then(setVersion).catch(reportFailure);
    listDevices().then(setDevices).catch(reportFailure);

    function reportFailure(cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  return (
    <main className="shell">
      <header>
        <h1>Peripheral</h1>
        <p className="build">{version || " "}</p>
      </header>

      <nav aria-label="Screens">
        <ul>
          {SCREENS.map((screen) => (
            <li key={screen}>
              <button type="button" disabled title="Not implemented (TICKET-13)">
                {screen}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <section aria-label="Devices">
        {error !== null ? (
          <p className="error">{error}</p>
        ) : devices === null ? (
          <p className="muted">Looking for devices…</p>
        ) : devices.length === 0 ? (
          <p className="muted">
            No device support exists yet. The transport layer arrives with
            TICKET-08.
          </p>
        ) : (
          <ul>
            {devices.map((device) => (
              <li key={device.id}>
                {device.label}
                {device.readOnly ? " (read-only)" : ""}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
