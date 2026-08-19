/**
 * Words for the ids that cross the boundary.
 *
 * The backend sends `he.actuation`, `no_evidence`, `safe_read`. Those are stable
 * identifiers that appear in journals and saved profiles; the sentences a person
 * reads are here, because wording is presentation and this project ships two
 * languages from its first release (spec.md, non-functional requirements). Only
 * English exists today; a second language is another map, not another backend.
 *
 * # Unknown ids are shown, never hidden
 *
 * Every lookup falls back to the id itself. A build whose backend knows a
 * capability this frontend has never heard of should show it as `he.rt.up`
 * rather than as a blank row — the same rule prior art puts on its own clients
 * (docs/prior-art/ui-prior-art.md, libratbag: "clients must ignore unknown
 * capabilities", meaning ignore, not misrepresent).
 */

const CAPABILITY_NAMES: Record<string, string> = {
  "he.actuation": "Actuation point",
};

const CAPABILITY_NOTES: Record<string, string> = {
  "he.actuation":
    "The depth at which a key first registers, measured from the top of travel. " +
    "Not how far a key is currently pressed, and not a rapid-trigger sensitivity: " +
    "those are different numbers behind different commands.",
};

export function capabilityName(id: string): string {
  return CAPABILITY_NAMES[id] ?? id;
}

export function capabilityNote(id: string): string | null {
  return CAPABILITY_NOTES[id] ?? null;
}

const FAMILY_REASONS: Record<string, string> = {
  no_evidence: "no product match and no independent protocol evidence",
  not_recorded: "the product is known; no family has been recorded for it",
  from_registry: "looked up through the product, and capped by how sure that is",
  from_protocol_evidence: "established by an exchange with this device",
};

export function familyReason(reason: string): string {
  return FAMILY_REASONS[reason] ?? reason;
}

const SIGNAL_NAMES: Record<string, string> = {
  interface_count: "interface count",
  collection_set: "collection set",
  descriptor_digest: "descriptor digest",
  report_profile: "report profile",
  vendor_id: "vendor id",
  product_id: "product id",
  manufacturer_string: "manufacturer string",
  product_string: "product string",
  release: "release",
  registry_claim: "registry claim",
  protocol_evidence: "protocol evidence",
};

export function signalName(signal: string): string {
  return SIGNAL_NAMES[signal] ?? signal;
}

const OUTCOMES: Record<string, string> = {
  ok: "completed",
  refused: "refused",
  failed: "failed",
  rejected: "answer rejected",
};

export function outcomeName(outcome: string): string {
  return OUTCOMES[outcome] ?? outcome;
}

/**
 * Local time, to the second.
 *
 * Seconds rather than milliseconds: the journal's ordering authority is the
 * monotonic offset the backend also sends, and printing milliseconds off a wall
 * clock would suggest a precision this column does not carry.
 */
export function formatTime(unixMs: number): string {
  return new Date(unixMs).toLocaleTimeString();
}
