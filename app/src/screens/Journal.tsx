/**
 * The Journal screen: everything the safety gate authorised, in order.
 *
 * # It is a product feature, not a debug log
 *
 * The question it exists to answer is "what did this program do to my keyboard
 * before it stopped working", and that question is asked by a person who is not
 * reading source. So refusals and failures are here alongside successes — a
 * journal of successes could not answer it — and every row is rendered from the
 * line the backend composed, so a screenshot of this table and a bug report from
 * the log say the same thing.
 *
 * # What is not here, by construction
 *
 * Payload bytes. Not truncated, not hashed, not "only for writes": the entry
 * type carries a length and no bytes, so there is nothing on this screen to
 * redact. A payload can hold a keymap, a keymap can hold a macro, and a macro
 * can hold whatever someone recorded into it — which in practice includes
 * passwords. This table is built to be screenshotted into a bug report without
 * anyone reviewing it first.
 */
import type { JournalRow } from "../ipc";
import { formatTime, outcomeName } from "../vocabulary";

export function JournalScreen({
  rows,
  onRefresh,
  busy,
}: {
  rows: JournalRow[];
  onRefresh: () => void;
  busy: boolean;
}) {
  return (
    <section className="panel">
      <h2>Journal</h2>
      <p className="muted">
        Every operation the safety gate authorised in this run — reads, probes,
        refusals and failures alike. Times are local; ordering follows the
        process clock in the last column.
      </p>

      <div className="actions">
        <button type="button" onClick={onRefresh} disabled={busy}>
          {busy ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="muted">
          Nothing yet. The first entry appears when something is sent to a
          device, which happens when you connect to one.
        </p>
      ) : (
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>time</th>
                <th>protocol</th>
                <th>command</th>
                <th>class</th>
                <th>intent</th>
                <th>bytes</th>
                <th>outcome</th>
                <th>+ms</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={`${row.atMs}-${row.command}-${row.deviceId}`}
                  className={row.outcome}
                >
                  <td className="mono">{formatTime(row.atUnixMs)}</td>
                  <td>{row.family}</td>
                  <td className="mono">{row.command}</td>
                  <td className="mono">{row.class}</td>
                  <td>{row.intent}</td>
                  <td className="mono">{row.payloadLen}</td>
                  <td>
                    {outcomeName(row.outcome)}
                    {row.detail !== null && (
                      <div className="muted faint">{row.detail}</div>
                    )}
                  </td>
                  <td className="mono">{row.atMs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
