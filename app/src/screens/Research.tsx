/**
 * Vetro Probe research flow — a thin product layer over the authoritative Probe
 * engine (Python sidecar, see app/src-tauri/src/probe and community/vetro_probe/
 * gui_rpc.py). This screen:
 *   - never calls transport/serializer/protocol code,
 *   - never constructs a plan itself (plan comes from probePlan()),
 *   - never enables an operation the backend planner classifies as BLOCKED,
 *   - refuses to start a run until recovery-first preflight is CLEAR,
 *   - renders recovery/restore outcomes honestly (FAIL_RESTORED vs MANUAL).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  onProbeEvents,
  probeClearRecovery,
  probeDiscover,
  probeOpenResults,
  probePlan,
  probeRecoveryStatus,
  probeStartRun,
  type ProbeDiscovery,
  type ProbeEngineEvent,
  type ProbePlan,
  type ProbeRecoveryStatus,
  type ProbeRunResult,
} from "../ipc";
import "./research.css";

type Screen =
  | { kind: "startup" }
  | { kind: "ready" }
  | { kind: "running" }
  | { kind: "result" }
  | { kind: "error"; message: string };

type OpState =
  | "QUEUED"
  | "BASELINING"
  | "TESTING"
  | "VERIFYING"
  | "RESTORING"
  | "PASS"
  | "BLOCKED"
  | "FAILED"
  | "RECOVERING";

export function ResearchScreen() {
  const [screen, setScreen] = useState<Screen>({ kind: "startup" });
  const [recovery, setRecovery] = useState<ProbeRecoveryStatus | null>(null);
  const [discovery, setDiscovery] = useState<ProbeDiscovery | null>(null);
  const [plan, setPlan] = useState<ProbePlan | null>(null);
  const [progress, setProgress] = useState<Record<string, OpState>>({});
  const [result, setResult] = useState<ProbeRunResult | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const unlistenRef = useRef<(() => void) | undefined>(undefined);

  const handleEngineEvent = useCallback((event: ProbeEngineEvent) => {
    if (event.kind === "progress") {
      setProgress((prev) => ({ ...prev, [event.progress.op]: event.progress.state }));
    } else {
      setResult(event.result);
      setScreen({ kind: "result" });
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const off = await onProbeEvents(handleEngineEvent);
      if (cancelled) {
        off();
        return;
      }
      unlistenRef.current = off;
      try {
        const [rec, disc, pl] = await Promise.all([
          probeRecoveryStatus(),
          probeDiscover(),
          probePlan(),
        ]);
        if (cancelled) return;
        setRecovery(rec);
        setDiscovery(disc);
        setPlan(pl);
        setScreen({ kind: "ready" });
      } catch (cause) {
        if (!cancelled) setScreen({ kind: "error", message: messageOf(cause) });
      }
    })();
    return () => {
      cancelled = true;
      unlistenRef.current?.();
    };
  }, [handleEngineEvent]);

  const start = useCallback(async () => {
    if (discovery?.state !== "IDENTIFIED") return;
    setScreen({ kind: "running" });
    setResult(null);
    setProgress({});
    try {
      const reply = await probeStartRun();
      if (!reply.started) {
        setScreen({ kind: "error", message: reply.error ?? "run did not start" });
      }
    } catch (cause) {
      setScreen({ kind: "error", message: messageOf(cause) });
    }
  }, [discovery]);

  const restoreConfirmed = useCallback(async () => {
    try {
      const rec = await probeClearRecovery();
      setRecovery(rec);
      if (rec.preflight === "CLEAR") {
        const disc = await probeDiscover();
        setDiscovery(disc);
      }
    } catch (cause) {
      setScreen({ kind: "error", message: messageOf(cause) });
    }
  }, []);

  if (screen.kind === "startup") {
    return <div className="research"><p className="muted">Checking previous session…</p></div>;
  }
  if (screen.kind === "error") {
    return (
      <div className="research" role="alert">
        <h2>Research is unavailable</h2>
        <p className="muted">{screen.message}</p>
      </div>
    );
  }

  const canStart =
    recovery?.preflight === "CLEAR" &&
    discovery?.state === "IDENTIFIED" &&
    screen.kind !== "running";

  const blocked = plan?.blocked ?? [];

  return (
    <div className="research">
      {recovery?.preflight === "RECOVERING" && (
        <section className="panel warn" role="alert">
          <h2>Restoring previous device state…</h2>
          <p className="muted">{recovery.reason}</p>
          <p>
            Research cannot start until the previous session is restored and verified.
          </p>
          <button type="button" onClick={() => void restoreConfirmed()}>
            I have restored the device (demo)
          </button>
        </section>
      )}

      {discovery?.state === "NO_DEVICE" && (
        <section className="panel">
          <h2>Connect your device</h2>
          <p className="muted">Plug in a compatible keyboard and reopen this screen.</p>
        </section>
      )}

      {discovery?.state === "FW_UNSUPPORTED" && (
        <section className="panel">
          <h2>This firmware is not yet supported</h2>
          <p className="muted">{discovery.reason}</p>
        </section>
      )}

      {(discovery?.state === "IDENTITY_MISMATCH" || discovery?.state === "UNSUPPORTED") && (
        <section className="panel">
          <h2>This device is not supported yet</h2>
          <p className="muted">{discovery.reason}</p>
        </section>
      )}

      {discovery?.state === "IDENTIFIED" && (
        <>
          <section className="panel">
            <h2>{discovery.device?.name ?? "Device detected"}</h2>
            <p className="muted">
              Firmware {discovery.device?.firmware ?? "unknown"} ·{" "}
              {discovery.supportedCount} safe checks available
            </p>
            {!canStart && recovery?.preflight !== "RECOVERING" && (
              <p className="muted">Start is disabled until recovery preflight clears.</p>
            )}
          </section>

          <section className="panel">
            <h3>Safe automatic checks</h3>
            <ul className="plan-list">
              {(plan?.safe ?? []).map((op) => (
                <li key={op.id}>
                  <span>{op.label}</span>
                  {screen.kind === "running" && <span className="op-state">{progress[op.id] ?? "QUEUED"}</span>}
                </li>
              ))}
            </ul>

            {blocked.length > 0 && (
              <>
                <h3>Not available yet</h3>
                <ul className="plan-list muted">
                  {blocked.map((op) => (
                    <li key={op.id}>
                      <span>{op.label}</span>
                      <span className="op-state">—</span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            <div className="actions">
              <button type="button" disabled={!canStart} onClick={() => void start()}>
                Start research
              </button>
              <button type="button" onClick={() => setShowDetails((v) => !v)}>
                {showDetails ? "Hide details" : "Technical details"}
              </button>
            </div>

            {showDetails && (
              <details open className="details">
                <summary>Technical details</summary>
                <pre className="mono">
                  {JSON.stringify(
                    {
                      discovery,
                      plan,
                      recovery,
                      progress,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            )}
          </section>
        </>
      )}

      {screen.kind === "result" && result && (
        <ResultView result={result} onShowDetails={setShowDetails} showDetails={showDetails} />
      )}
    </div>
  );
}

function ResultView({
  result,
  showDetails,
  onShowDetails,
}: {
  result: ProbeRunResult;
  showDetails: boolean;
  onShowDetails: (v: boolean) => void;
}) {
  if (result.status === "FAILED_REQUIRES_MANUAL_RESTORE") {
    return (
      <section className="panel warn" role="alert">
        <h2>Research stopped</h2>
        <p className="muted">
          Your original device settings could not be verified as restored. Please
          restore them using the vendor software, then come back.
        </p>
        <button type="button" onClick={() => onShowDetails(!showDetails)}>
          Technical details
        </button>
        {showDetails && <Details result={result} />}
      </section>
    );
  }

  if (result.status === "FAIL_RESTORED") {
    return (
      <section className="panel">
        <h2>Research stopped</h2>
        <p className="muted">
          Your original device settings were restored successfully.
        </p>
        <p className="muted">
          {result.checksCompleted} of {result.checksTotal} checks completed.
        </p>
        <button type="button" onClick={() => onShowDetails(!showDetails)}>
          Technical details
        </button>
        {showDetails && <Details result={result} />}
      </section>
    );
  }

  return (
    <section className="panel">
      <h2>Research complete</h2>
      <p className="muted">Device restored successfully.</p>
      <p className="muted">{result.checksCompleted} checks completed.</p>
      <p className="muted">Results ready.</p>
      <div className="actions">
        <button type="button" onClick={() => onShowDetails(!showDetails)}>
          {showDetails ? "Hide details" : "View details"}
        </button>
        <button type="button" onClick={() => void probeOpenResults()}>
          Open results folder
        </button>
      </div>
      {showDetails && <Details result={result} />}
    </section>
  );
}

function Details({ result }: { result: ProbeRunResult }) {
  return (
    <details open className="details">
      <summary>Technical details</summary>
      <pre className="mono">
        {JSON.stringify(
          {
            status: result.status,
            restored: result.restored,
            checksCompleted: result.checksCompleted,
            checksTotal: result.checksTotal,
            evidenceSource: result.evidenceSource,
            physicalValidationEvidence: result.physicalValidationEvidence,
            outputPath: result.outputPath,
            results: result.results,
          },
          null,
          2,
        )}
      </pre>
    </details>
  );
}

function messageOf(cause: unknown): string {
  if (typeof cause === "string") return cause;
  if (cause instanceof Error) return cause.message;
  return String(cause);
}
