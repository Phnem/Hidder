/*
 * HERO 84 HE — consolidated physical validation batch, 2026-08-24.
 *
 * Paste one phase at a time into the Peripheral dev-build DevTools console and
 * send back what it prints. Each phase prints one JSON object and each is
 * independent, so a failure in one costs nothing in the others.
 *
 * Prerequisites: `npm run tauri dev` in the worktree, the HERO 84 HE plugged
 * in, and the vendor's own software NOT running (it holds the endpoint).
 *
 * Everything here goes through the same typed IPC any screen would use. There
 * is no opcode in this file: `firstExchange`/`firstWrite` take an ACL command
 * *name*, which `pcore` resolves through a closed Rust enum, and a name that is
 * not a command awaiting a first exchange is refused before a session is
 * touched.
 *
 * Phases 1-3 are reversible and read their own work back. Phase 4 disconnects
 * the keyboard on purpose and is last for that reason.
 */

// --- shared helpers ---------------------------------------------------------

const D = () => {
  const d = window.prtscDebug;
  if (!d) throw new Error("prtscDebug is missing — is this the dev build?");
  return d;
};

/** The data section of a reply frame: length byte at 5, bytes from 6. */
const dataOf = (hex) => {
  const b = [];
  for (let i = 0; i < hex.length; i += 2) b.push(parseInt(hex.slice(i, i + 2), 16));
  return b.slice(6, 6 + b[5]);
};

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const connectFirst = async (d) => {
  const devices = await d.listDevices();
  const board = devices.find((x) => (x.product ?? x.label ?? "").match(/HERO|AULA/i)) ?? devices[0];
  if (!board) throw new Error("no device enumerated");
  await d.connectDevice(board.id);
  return board;
};

// ============================================================================
// PHASE 1 — reads only. Sends sixteen commands that have never been sent from
// here, once each, plus the production actuation read. Changes nothing.
// ============================================================================

const PHASE_1 = async () => {
  const d = D();
  const out = { phase: 1, at: new Date().toISOString() };
  const board = await connectFirst(d);
  out.device = {
    id: board.id,
    label: board.label,
    product: board.product,
    modelId: board.modelId,
    exact: board.identity,
  };

  const targets = [
    "read_firmware_version",
    "read_rt_precision",
    "read_supported_switches",
    "read_profile_index",
    "read_profile_name_slot0",
    "read_profile_name_slot1",
    "read_profile_name_slot2",
    "read_os_mode",
    "read_win_lock",
    "read_polling_rate",
    "read_key_combo",
    "read_auto_calibrate",
    "read_light_mode",
    "read_rapid_trigger",
    "read_deadzone",
    "read_key_switch_type",
  ];

  out.probes = {};
  for (const target of targets) {
    try {
      const r = await d.firstExchange(board.id, target);
      out.probes[target] = {
        echoedRequest: r.echoedRequest,
        data: dataOf(r.replyHex),
        hex: r.replyHex.slice(0, 40),
        decoded: r.decoded,
      };
    } catch (error) {
      out.probes[target] = { error: String(error) };
    }
    await wait(120);
  }

  // The production actuation read, as records. The third number in each is the
  // record's trailing byte -- the subject of the open read-back incident.
  try {
    out.actuationRecords = await d.readActuationRecords(board.id);
  } catch (error) {
    out.actuationRecords = { error: String(error) };
  }

  console.log(JSON.stringify(out, null, 1));
  return out;
};

// ============================================================================
// PHASE 2 — write round-trips. For each setting: read, write, read back, roll
// back, read again. Every value is minimal and every change is undone.
//
// `profile_index` is included and is the widest write here: switching profiles
// changes what every key does at once. It is not destructive and switching back
// restores it -- the vendor's own software performed exactly this cycle on this
// board on 2026-08-22 -- but if you would rather not, delete its entry from
// PLAN below and the rest still runs.
// ============================================================================

const PHASE_2 = async () => {
  const d = D();
  const out = { phase: 2, at: new Date().toISOString(), results: {} };
  const board = await connectFirst(d);
  out.device = { id: board.id, label: board.label };

  // [read command, write command, how to derive the changed value]
  const PLAN = [
    ["read_win_lock", "set_win_lock", (v) => [v[0] ^ 1]],
    ["read_os_mode", "set_os_mode", (v) => [v[0] ^ 1]],
    ["read_key_combo", "set_key_combo", (v) => [v[0] ^ 1]],
    ["read_auto_calibrate", "set_auto_calibrate", (v) => [v[0] ^ 1]],
    // A different slot, then back. 0..2 exist.
    ["read_profile_index", "set_profile_index", (v) => [(v[0] + 1) % 3]],
    // One unit of red. The smallest change this register can carry, and the
    // one the vendor's own UI makes when a colour is nudged.
    ["read_light_mode", "set_light_mode", (v) => [v[0], v[1], v[2] ^ 1, v[3], v[4], v[5], v[6]]],
    // W's press sensitivity, one unit. Rapid trigger's enable byte is left
    // exactly as it was: turning it on changes how the key feels.
    ["read_rapid_trigger", "set_rapid_trigger", (v) => {
      const r = v.slice(0, 8);
      r[4] = (r[4] + 1) & 0xff;
      return r;
    }],
    // W's upper dead zone, one unit. Enable left as it was, for the same
    // reason.
    ["read_deadzone", "set_deadzone", (v) => {
      const r = v.slice(0, 8);
      r[3] = (r[3] + 1) & 0xff;
      return r;
    }],
  ];

  for (const [readName, writeName, change] of PLAN) {
    const step = { read: readName, write: writeName };
    try {
      const before = await d.firstExchange(board.id, readName);
      step.baseline = { data: dataOf(before.replyHex), echo: before.echoedRequest };
      if (before.echoedRequest) {
        step.skipped = "the read echoed the request; there is no baseline to write against";
        out.results[writeName] = step;
        continue;
      }

      // Analog records are per key; only the first record is written.
      const width = writeName === "set_rapid_trigger" || writeName === "set_deadzone" ? 8 : undefined;
      const previous = width ? step.baseline.data.slice(0, width) : step.baseline.data;
      const wanted = change(previous);
      step.previous = previous;
      step.wanted = wanted;

      const write = await d.firstWrite(board.id, writeName, previous, wanted);
      step.write = { attempt: write.attempt, rollbackHex: write.rollbackHex };
      await wait(300);

      const after = await d.firstExchange(board.id, readName);
      step.readback = dataOf(after.replyHex).slice(0, previous.length);
      step.landed = JSON.stringify(step.readback) === JSON.stringify(wanted);

      const back = await d.firstWrite(board.id, writeName, wanted, previous);
      step.rollback = { attempt: back.attempt };
      await wait(300);

      const restored = await d.firstExchange(board.id, readName);
      step.restored = dataOf(restored.replyHex).slice(0, previous.length);
      step.restoredOk = JSON.stringify(step.restored) === JSON.stringify(previous);
    } catch (error) {
      step.error = String(error);
    }
    out.results[writeName] = step;
    await wait(200);
  }

  console.log(JSON.stringify(out, null, 1));
  return out;
};

// ============================================================================
// PHASE 3 — the actuation read-back incident.
//
// Three reads and one write. The question is whether Peripheral's own
// `set_key_travel` -- which writes `0` into the record's fifth byte -- flips
// that byte on the key it touches. If W's third number goes 1 -> 0 while A, S
// and D's stay put, the open incident has a cause.
//
// The write is a real actuation change on W and is rolled back to whatever the
// first read reported. Cadence waits are the ACL's own declared numbers.
// ============================================================================

const PHASE_3 = async () => {
  const d = D();
  const out = { phase: 3, at: new Date().toISOString() };
  const board = await connectFirst(d);
  out.device = { id: board.id, label: board.label };

  out.before = await d.readActuationRecords(board.id);
  const w = out.before.find((r) => r[0] === 30);
  if (!w) {
    out.error = "no record for W (key id 30)";
    console.log(JSON.stringify(out, null, 1));
    return out;
  }
  // One raw unit, in whichever direction stays inside the bounds this project
  // will send. The size does not matter; that a write happened does.
  const fromMm = w[1] / 100;
  const toMm = Math.round((fromMm + 0.05) * 100) / 100;
  out.plan = { key: "W", fromRaw: w[1], fromMm, toMm, trailingBefore: w[2] };

  await wait(1100);
  out.write = await d.writeActuation(board.id, "W", toMm);

  await wait(2700);
  out.after = await d.readActuationRecords(board.id);
  const afterW = out.after.find((r) => r[0] === 30);
  out.trailingAfter = afterW ? afterW[2] : null;
  out.trailingFlipped = afterW ? w[2] !== afterW[2] : null;
  out.otherKeysUnchanged = out.before
    .filter((r) => r[0] !== 30)
    .every((r) => {
      const now = out.after.find((x) => x[0] === r[0]);
      return now && now[1] === r[1] && now[2] === r[2];
    });

  await wait(1100);
  out.rollback = await d.writeActuation(board.id, "W", fromMm);
  await wait(2700);
  out.restored = await d.readActuationRecords(board.id);

  console.log(JSON.stringify(out, null, 1));
  return out;
};

// ============================================================================
// PHASE 4 — polling. THIS DISCONNECTS THE KEYBOARD ON PURPOSE.
//
// The write is never acknowledged: the board re-enumerates immediately and the
// handle dies. That is the success path, not a failure. Run 4a, wait for the
// keyboard to come back (a second or two -- Windows will re-detect it), then
// run 4b.
//
// If anything goes wrong the value is in NVM and survives a reboot; 4c puts it
// back to 125 Hz explicitly.
// ============================================================================

const PHASE_4A = async () => {
  const d = D();
  const out = { phase: "4a", at: new Date().toISOString() };
  const board = await connectFirst(d);
  const before = await d.firstExchange(board.id, "read_polling_rate");
  out.baseline = dataOf(before.replyHex);
  // 3 == 125 Hz, 2 == 250 Hz. The only two values anybody has exercised on
  // hardware, in either direction.
  const to = out.baseline[0] === 3 ? 2 : 3;
  out.requested = [to];
  try {
    out.write = await d.firstWrite(board.id, "set_polling_rate", out.baseline, [to]);
  } catch (error) {
    out.write = { error: String(error) };
  }
  console.log(JSON.stringify(out, null, 1));
  console.log("Now wait for the keyboard to re-enumerate, then run PHASE_4B().");
  return out;
};

const PHASE_4B = async () => {
  const d = D();
  const out = { phase: "4b", at: new Date().toISOString() };
  const board = await connectFirst(d);
  // The identity re-check the transaction requires: the board that came back
  // has to be the one that went away.
  out.device = { id: board.id, modelId: board.modelId, label: board.label };
  const after = await d.firstExchange(board.id, "read_polling_rate");
  out.observed = dataOf(after.replyHex);
  console.log(JSON.stringify(out, null, 1));
  return out;
};

const PHASE_4C = async () => {
  const d = D();
  const out = { phase: "4c", at: new Date().toISOString() };
  const board = await connectFirst(d);
  const now = dataOf((await d.firstExchange(board.id, "read_polling_rate")).replyHex);
  out.observed = now;
  if (now[0] === 3) {
    out.note = "already 125 Hz, nothing to do";
  } else {
    out.rollback = await d.firstWrite(board.id, "set_polling_rate", now, [3]);
    out.note = "written; the board will disconnect again, then re-run PHASE_4B to confirm";
  }
  console.log(JSON.stringify(out, null, 1));
  return out;
};

// Expose them so a phase can be run by name from the console.
Object.assign(window, { PHASE_1, PHASE_2, PHASE_3, PHASE_4A, PHASE_4B, PHASE_4C });
console.log("loaded — run PHASE_1() first");
