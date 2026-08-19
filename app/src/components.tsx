/**
 * The small pieces every screen needs, so the same fact looks the same twice.
 *
 * Chiefly: confidence badges. How sure the application is about something is
 * shown on three screens, and three components deciding independently which
 * colour "candidate" gets is how a user learns that the colours mean nothing.
 */
import type { ConnectionView, Confidence } from "./ipc";

type Tone = "verified" | "warn" | "danger" | "neutral";

export function Badge({
  tone,
  children,
}: {
  tone: Tone;
  children: React.ReactNode;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

/**
 * A confidence, with the word alongside the colour.
 *
 * Never colour alone. The scale runs unknown → candidate → high → verified and
 * only the last of those authorises anything, which is too load-bearing a
 * distinction to carry in hue on a screen someone may be reading in greyscale
 * or with a colour vision deficiency.
 */
export function ConfidenceBadge({ value }: { value: Confidence }) {
  const tone: Tone =
    value === "verified" ? "verified" : value === "unknown" ? "neutral" : "warn";
  return <Badge tone={tone}>{value}</Badge>;
}

/** One line summarising where a device stands, in words a person can act on. */
export function connectionSummary(connection: ConnectionView): {
  tone: Tone;
  label: string;
  detail: string | null;
} {
  switch (connection.state) {
    case "connected":
      return { tone: "verified", label: "connected", detail: null };
    case "ready":
      return {
        tone: "neutral",
        label: "not connected",
        detail: "A configuration channel was recognised. Nothing has been sent to it.",
      };
    case "noConfigEndpoint":
      return {
        tone: "neutral",
        label: "no configuration channel",
        detail:
          "This device exposes nothing this application knows how to speak on. " +
          "That is a complete answer, not a failure — most HID devices are like this.",
      };
    case "unreachable":
      return {
        tone: "warn",
        label: "channel would not open",
        detail:
          connection.userMessage +
          (connection.vendorSoftwareSuspected
            ? " The usual cause is another application holding it — the vendor's own " +
              "configurator, most often. This has not been established, only suspected: " +
              "close any vendor software and try again."
            : ""),
      };
    case "stalled":
      return { tone: "danger", label: "endpoint stalled", detail: connection.userMessage };
  }
}
