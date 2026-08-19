/**
 * The Hall-effect screen: a keyboard, the values read off it, and where each
 * number came from.
 *
 * # Read-only, and structurally so
 *
 * There is not a disabled slider on this screen. TICKET-13 is read-only
 * symmetrically with TICKET-12, and the rule above it is stronger than "disable
 * the control": a capability with no confirmed command behind it is not
 * rendered as a control at all. That rule is not enforced by discipline here —
 * it is enforced by the vocabulary. `pcaps::CapId` is a closed enum with one
 * variant, so a rapid-trigger control has nothing to bind to and cannot be
 * written by mistake. When a rapid-trigger command is verified, the variant
 * appears, the backend reports the slot, and this screen grows a section. Not
 * before.
 *
 * # Shaped after the prior art
 *
 * The arrangement — a keyboard widget, key selection, and a panel of settings
 * for the selection — is the one every Hall-effect configurator in the notes
 * uses, because it is the one that matches how a person thinks about per-key
 * settings (docs/prior-art/ui-prior-art.md: Pipette's Actuation tab, Vial's
 * keymap grid). What is deliberately *not* borrowed is presenting a per-key
 * setting as a number the screen owns: every value here carries the command that
 * produced it and the scale that converted it.
 */
import { useMemo } from "react";
import type { CapabilityReading, DeviceCard } from "../ipc";
import { canRead, capabilitySlot, valuesByLabel } from "../model";
import { Badge, ConfidenceBadge } from "../components";
import { PLACEHOLDER_75, layoutKeyCount, layoutLabels } from "../layout";
import { capabilityName, capabilityNote, formatTime } from "../vocabulary";

export const ACTUATION = "he.actuation";

export function HeScreen({
  device,
  reading,
  busy,
  selectedKey,
  onSelectKey,
  onRead,
}: {
  device: DeviceCard | null;
  reading: CapabilityReading | null;
  busy: boolean;
  selectedKey: string | null;
  onSelectKey: (id: string | null) => void;
  onRead: () => void;
}) {
  const labels = useMemo(() => layoutLabels(PLACEHOLDER_75), []);
  const values = useMemo(
    () => valuesByLabel(reading, labels),
    [reading, labels],
  );

  if (device === null) {
    return (
      <section className="panel">
        <h2>No device selected</h2>
        <p className="muted">Choose a device on the Devices screen first.</p>
      </section>
    );
  }

  const slot = capabilitySlot(device, ACTUATION);
  const readable = canRead(device, ACTUATION);

  return (
    <div className="he">
      <section className="panel">
        <h2>{capabilityName(ACTUATION)}</h2>
        <p className="muted">{capabilityNote(ACTUATION)}</p>

        {slot === undefined ? (
          <p className="muted">
            This build's backend reports no such capability.
          </p>
        ) : slot.availability.state !== "readable" ? (
          <p className="muted">
            <Badge tone="neutral">not available</Badge>{" "}
            {slot.availability.state === "noVerifiedCommand"
              ? "There is no verified command for this on this device's protocol. " +
                "That says nothing about whether the hardware has the feature."
              : slot.availability.state === "notPresent"
                ? `Established that this device does not have it: ${slot.availability.evidence}`
                : "Readable, not read yet."}
          </p>
        ) : null}

        <div className="actions">
          <button
            type="button"
            className="primary"
            onClick={onRead}
            disabled={!readable || busy}
            title={
              readable
                ? "Sends one read command through the safety gate"
                : "Connect to the device first"
            }
          >
            {busy ? "Reading…" : reading === null ? "Read actuation" : "Read again"}
          </button>
        </div>

        <Keyboard
          values={values}
          selectedKey={selectedKey}
          onSelectKey={onSelectKey}
        />

        <div className="legend-key">
          <span>
            <i className="swatch read" /> read from this board
          </span>
          <span>
            <i className="swatch unread" /> not read by this build
          </span>
        </div>

        <p className="muted faint">
          The layout above is a placeholder: a generic 75% arrangement of{" "}
          {layoutKeyCount(PLACEHOLDER_75)} keys written into this application, not
          read from the device and not verified against it. Real per-model layouts
          arrive with the registry's layout table.
        </p>
      </section>

      <aside className="panel">
        <Detail
          reading={reading}
          selectedKey={selectedKey}
          values={values}
          readable={readable}
        />
      </aside>
    </div>
  );
}

function Keyboard({
  values,
  selectedKey,
  onSelectKey,
}: {
  values: Map<string, { measurement: { rendered: string } }>;
  selectedKey: string | null;
  onSelectKey: (id: string | null) => void;
}) {
  return (
    <div className="keyboard" role="group" aria-label="Keyboard layout">
      {PLACEHOLDER_75.rows.map((keys, rowIndex) => (
        <div className="row" key={rowIndex}>
          {keys.map((key) => {
            const value = values.get(key.label);
            const classes = [
              "key",
              value !== undefined ? "has-value" : "",
              key.id === selectedKey ? "selected" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <button
                type="button"
                key={key.id}
                className={classes}
                style={{ "--w": key.w ?? 1 } as React.CSSProperties}
                aria-pressed={key.id === selectedKey}
                onClick={() =>
                  onSelectKey(key.id === selectedKey ? null : key.id)
                }
              >
                <span className="legend">{key.label}</span>
                {value !== undefined && (
                  <span className="value">{value.measurement.rendered}</span>
                )}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/**
 * The panel that answers "why should I believe this number".
 *
 * Every reading shows its origin, the command that earned it and the provenance
 * line the backend wrote. That line is not debug output: the design rule is that
 * a person can find out why a value is trusted without opening the source, and a
 * capability that cannot explain itself is one nobody can audit.
 */
function Detail({
  reading,
  selectedKey,
  values,
  readable,
}: {
  reading: CapabilityReading | null;
  selectedKey: string | null;
  values: Map<string, { label: string; measurement: { rendered: string } }>;
  readable: boolean;
}) {
  if (reading === null) {
    return (
      <>
        <h2>Nothing read yet</h2>
        <p className="muted">
          {readable
            ? "Press Read actuation. One command goes to the device; nothing is written."
            : "Connect to a device whose protocol is established, and the read becomes available."}
        </p>
      </>
    );
  }

  const selectedLabel = selectedKey?.split("#")[0] ?? null;
  const selectedValue =
    selectedLabel === null ? undefined : values.get(selectedLabel);

  return (
    <>
      <h2>{capabilityName(reading.id)}</h2>

      {selectedLabel !== null && (
        <p>
          <strong>{selectedLabel}</strong>{" "}
          {selectedValue !== undefined ? (
            <span className="mono">{selectedValue.measurement.rendered}</span>
          ) : (
            <span className="muted">
              not read — this build reads four keys, not the board
            </span>
          )}
        </p>
      )}

      <h3>Values</h3>
      {reading.value.shape === "perKey" ? (
        <table>
          <tbody>
            {reading.value.keys.map((key) => (
              <tr key={key.label}>
                <td>{key.label}</td>
                <td className="mono">{key.measurement.rendered}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">
          This capability came back shaped as {reading.value.shape}, which this
          screen does not draw on a keyboard.
        </p>
      )}

      <h3>Where this came from</h3>
      <dl className="facts">
        <dt>origin</dt>
        <dd>
          {reading.fromHardware ? (
            <Badge tone="verified">{reading.origin}</Badge>
          ) : (
            <Badge tone="warn">{reading.origin}</Badge>
          )}
        </dd>
        <dt>confidence</dt>
        <dd>
          <ConfidenceBadge value={reading.confidence} />
        </dd>
        {reading.originCommand !== null && (
          <>
            <dt>command</dt>
            <dd className="mono">{reading.originCommand}</dd>
          </>
        )}
        <dt>read at</dt>
        <dd>{formatTime(reading.readAtUnixMs)}</dd>
      </dl>
      <p className="muted">{reading.provenance}</p>
    </>
  );
}
