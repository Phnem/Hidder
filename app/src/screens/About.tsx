/**
 * About: which build this is, and what it is honestly able to do.
 *
 * The second half is the part that matters. This application reads four keys on
 * one board and writes nothing, and a person who installs it should learn that
 * here rather than by exploring an interface that implies more.
 */
import type { DeviceCard } from "../ipc";

export function AboutScreen({
  version,
  devices,
}: {
  version: string;
  devices: DeviceCard[];
}) {
  const writable = devices.filter((device) => device.writes.enabled).length;

  return (
    <section className="panel">
      <h2>Peripheral</h2>
      <p className="muted mono">{version || "unknown build"}</p>

      <h3>What this build can do</h3>
      <ul className="muted">
        <li>Enumerate every HID device and identify it on three separate axes.</li>
        <li>
          Open a session with a device whose protocol family it can establish by
          asking, and read its actuation points.
        </li>
        <li>
          Write nothing. Not "writing is disabled" — a build's write commands come
          from a generated allow-list, and this one contains none.{" "}
          {writable === 0
            ? "No connected device has a write command available."
            : `${writable} connected device(s) report write commands available, which should not happen in this build.`}
        </li>
      </ul>

      <h3>What it deliberately does not do</h3>
      <ul className="muted">
        <li>Flash firmware. Not in v1, under any circumstance.</li>
        <li>
          Take an endpoint from another application. If the vendor's software is
          holding it, this says so and stops.
        </li>
        <li>
          Ask a device anything to refresh an indicator. Device state arrives
          here on its own; nothing on screen causes traffic.
        </li>
        <li>Require an account, or make any network call.</li>
      </ul>

      <h3>Instance identifiers</h3>
      <p className="muted">
        Serial numbers are never read into anything this application shows or
        exports. Whether one exists is a fingerprint signal; what it says
        identifies one physical unit and is of no use in deciding what a device
        is.
      </p>
    </section>
  );
}
