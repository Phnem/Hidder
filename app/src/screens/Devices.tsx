/**
 * The Devices screen: what is plugged in, what we make of it, and one action.
 *
 * # Shaped after the prior art, for the reason the prior art has it
 *
 * A list of cards, one per device, with a status and an explicit connect step —
 * the arrangement Piper and Pipette both settled on
 * (docs/prior-art/ui-prior-art.md). Connecting is a button rather than something
 * that happens on arrival, because on this project connecting means *sending the
 * device a command*, and the vendor's own software may be holding the endpoint.
 * The stated policy is to detect that and say so rather than race for it.
 *
 * # Why the card shows three confidences and not one
 *
 * Because there is no such number as "how sure we are about this device".
 * Knowing exactly which product is plugged in while knowing nothing about its
 * opcode vocabulary is an ordinary state, and it is only the third axis that
 * decides whether anything may be written. A single blended figure would be the
 * matcher's own worst failure mode reproduced on screen.
 */
import type { DeviceCard } from "../ipc";
import { canConnect, isConnected, isStalled, writeControlsEnabled } from "../model";
import { Badge, ConfidenceBadge, connectionSummary } from "../components";
import { capabilityName, familyReason } from "../vocabulary";

export function DevicesScreen({
  devices,
  selectedId,
  busyId,
  onSelect,
  onConnect,
  onDisconnect,
}: {
  devices: DeviceCard[];
  selectedId: number | null;
  busyId: number | null;
  onSelect: (id: number) => void;
  onConnect: (id: number) => void;
  onDisconnect: (id: number) => void;
}) {
  if (devices.length === 0) {
    return (
      <section className="panel">
        <h2>No devices</h2>
        <p className="muted">
          Nothing is enumerating. Plug a device in — the list updates on its own,
          without this window asking any hardware anything.
        </p>
        <p className="muted faint">
          On Linux a device can enumerate for the system and still be unreadable
          here without a udev rule.
        </p>
      </section>
    );
  }

  return (
    <div className="cards">
      {devices.map((device) => (
        <DeviceCardView
          key={device.id}
          device={device}
          selected={device.id === selectedId}
          busy={device.id === busyId}
          onSelect={() => onSelect(device.id)}
          onConnect={() => onConnect(device.id)}
          onDisconnect={() => onDisconnect(device.id)}
        />
      ))}
    </div>
  );
}

function DeviceCardView({
  device,
  selected,
  busy,
  onSelect,
  onConnect,
  onDisconnect,
}: {
  device: DeviceCard;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const status = connectionSummary(device.connection);

  return (
    <article
      className={`card${selected ? " selected" : ""}`}
      onClick={onSelect}
      aria-label={device.label}
    >
      <header>
        <div>
          <h2>{device.label}</h2>
          <p className="hardware-id mono">{device.hardwareId}</p>
        </div>
        <Badge tone={status.tone}>{status.label}</Badge>
      </header>

      {status.detail !== null && <p className="muted">{status.detail}</p>}

      <dl className="facts">
        <dt>product</dt>
        <dd>
          {device.identity.product.answer ?? "not in the registry"}{" "}
          <ConfidenceBadge value={device.identity.product.confidence} />
        </dd>

        <dt>structure</dt>
        <dd>
          <span className="mono">{device.identity.structural.answer}</span>{" "}
          <ConfidenceBadge value={device.identity.structural.confidence} />
        </dd>

        <dt>protocol</dt>
        <dd>
          {device.identity.family.family ?? "not established"}{" "}
          <ConfidenceBadge value={device.identity.family.confidence} />
          <div className="muted faint">
            {familyReason(device.identity.family.reason)}
          </div>
        </dd>

        <dt>serial</dt>
        <dd className="muted">
          {device.serialPresent ? "present (not read)" : "none reported"}
        </dd>

        {device.modelId !== null && (
          <>
            <dt>model id</dt>
            <dd className="mono">{device.modelId}</dd>
          </>
        )}

        <dt>writes</dt>
        <dd>
          <WriteState device={device} />
        </dd>
      </dl>

      <h3>Capabilities</h3>
      <ul className="muted" style={{ margin: 0, paddingLeft: "1.1rem" }}>
        {device.capabilities.map((slot) => (
          <li key={slot.id}>
            {capabilityName(slot.id)} — <AvailabilityText slot={slot} />
          </li>
        ))}
      </ul>

      <DeviceActions
        device={device}
        busy={busy}
        onConnect={onConnect}
        onDisconnect={onDisconnect}
      />
    </article>
  );
}

/**
 * The one action a card offers, or none.
 *
 * A device with no configuration channel gets **no button at all**, rather than a
 * permanently dead one. On an ordinary machine most HID devices are in that
 * state — a mouse, a webcam, a motherboard's LED controller — and a row of
 * greyed-out Connect buttons across all of them reads as an application that is
 * broken rather than as devices it was never going to configure. The card
 * already says so in words.
 *
 * A stalled device is the opposite case and does get a disabled button: it is a
 * device this application *was* talking to, so the absence of the action needs
 * explaining rather than assuming.
 */
function DeviceActions({
  device,
  busy,
  onConnect,
  onDisconnect,
}: {
  device: DeviceCard;
  busy: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  if (device.connection.state === "noConfigEndpoint") {
    return null;
  }

  return (
    <div className="actions" onClick={(event) => event.stopPropagation()}>
      {isConnected(device) ? (
        <button type="button" onClick={onDisconnect} disabled={busy}>
          Disconnect
        </button>
      ) : (
        <button
          type="button"
          className="primary"
          onClick={onConnect}
          disabled={busy || !canConnect(device)}
          title={
            isStalled(device)
              ? "Reconnect the device physically first. Reopening a stalled endpoint does not clear it, and further attempts keep it stalled."
              : "Sends one read command and establishes the protocol family from the answer"
          }
        >
          {busy ? "Connecting…" : "Connect"}
        </button>
      )}
    </div>
  );
}

/**
 * Both halves of "can this be written to", never one.
 *
 * A build whose family reached `verified` but which has no write command in its
 * ACL would report "permitted" from the first half alone and be unable to write
 * anything. Today every build is in exactly that state, which is why the second
 * number is on screen rather than assumed.
 */
function WriteState({ device }: { device: DeviceCard }) {
  if (writeControlsEnabled(device)) {
    return <Badge tone="warn">write commands available</Badge>;
  }
  const why = !device.writes.familyPermits
    ? "the protocol family is not verified"
    : "this build has no reviewed write command for this family";
  return (
    <>
      <Badge tone="neutral">read-only</Badge>
      <div className="muted faint">
        {why} ({device.writes.commandsAvailable} write commands exist)
      </div>
    </>
  );
}

function AvailabilityText({ slot }: { slot: DeviceCard["capabilities"][number] }) {
  switch (slot.availability.state) {
    case "readable":
      return <span>readable</span>;
    case "notRead":
      return <span>not read yet</span>;
    case "noVerifiedCommand":
      return (
        <span title="Says nothing about whether the hardware has the feature.">
          no verified command on this protocol
        </span>
      );
    case "notPresent":
      return <span>not present on this device ({slot.availability.evidence})</span>;
  }
}
