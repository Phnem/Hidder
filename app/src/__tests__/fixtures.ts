/**
 * Cards and readings to test against, built to look like the real ones.
 *
 * The numbers are the ones the board actually returned on 2026-08-18 — W 0.51,
 * A 1.02, S 1.49, D 2.00 mm, written by the vendor's own configurator and read
 * back by this project (docs/hardware/aula-bytech-exchange-005-actuation-verified.md).
 * Using the recorded values rather than round ones means a fixture that drifts
 * from the hardware record is a fixture somebody has to justify.
 */
import type { CapabilityReading, DeviceCard } from "../ipc";

export function card(overrides: Partial<DeviceCard> = {}): DeviceCard {
  return {
    id: 0x372e103e,
    hardwareId: "372E:103E",
    label: "BY Tech HERO 84 HE",
    product: "HERO 84 HE",
    manufacturer: "BY Tech",
    serialPresent: false,
    identity: {
      structural: {
        answer: "4677b3488a43cfc9",
        confidence: "verified",
        signals: [],
      },
      product: { answer: "aula-hero84-he", confidence: "verified", signals: [] },
      family: {
        family: null,
        confidence: "unknown",
        reason: "not_recorded",
        permitsWrite: false,
        signals: [],
      },
    },
    connection: { state: "ready" },
    capabilities: [
      { id: "he.actuation", availability: { state: "noVerifiedCommand" } },
    ],
    writes: { familyPermits: false, commandsAvailable: 0, enabled: false },
    modelId: null,
    ...overrides,
  };
}

/** The same board once a session has established what it speaks. */
export function connectedCard(overrides: Partial<DeviceCard> = {}): DeviceCard {
  return card({
    connection: { state: "connected" },
    identity: {
      ...card().identity,
      family: {
        family: "aula-bytech",
        confidence: "verified",
        reason: "from_protocol_evidence",
        permitsWrite: true,
        signals: [],
      },
    },
    capabilities: [{ id: "he.actuation", availability: { state: "readable" } }],
    writes: { familyPermits: true, commandsAvailable: 0, enabled: false },
    modelId: 84,
    ...overrides,
  });
}

export function actuationReading(
  overrides: Partial<CapabilityReading> = {},
): CapabilityReading {
  return {
    id: "he.actuation",
    origin: "verified on hardware",
    originCommand: "read_key_travel",
    fromHardware: true,
    confidence: "verified",
    provenance:
      "read with command read_key_travel; scale 0.01 mm per step, the vendor's documented fallback",
    value: {
      shape: "perKey",
      keys: [
        key("W", 0.51),
        key("A", 1.02),
        key("S", 1.49),
        key("D", 2.0),
      ],
    },
    readAtUnixMs: 1_700_000_000_000,
    ...overrides,
  };
}

function key(label: string, mm: number) {
  return {
    label,
    measurement: {
      value: mm,
      unit: "mm",
      decimals: 2,
      rendered: `${mm.toFixed(2)} mm`,
    },
  };
}
