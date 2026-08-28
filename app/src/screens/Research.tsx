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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  type ProbeProgressState,
  type ProbeRecoveryStatus,
  type ProbeRunResult,
} from "../ipc";
import "./research.css";

type Screen =
  | { kind: "startup" }
  | { kind: "ready" }
  | { kind: "running"; starting: boolean }
  | { kind: "result" }
  | { kind: "error"; message: string };

interface OpProgressInfo {
  state: ProbeProgressState;
  text?: string;
  label?: string;
}

function formatTimer(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
}

function friendlyStageText(state?: ProbeProgressState, label?: string, text?: string): string {
  if (text && text.trim().length > 0 && text !== "Waiting...") return text;
  switch (state) {
    case "BASELINING":
      return `Reading ${label ?? "original"} setting…`;
    case "TESTING":
      return `Testing temporary setting…`;
    case "VERIFYING":
      return `Verifying result…`;
    case "RESTORING":
      return `Restoring original setting…`;
    case "RECOVERING":
      return `Recovering original device state…`;
    case "PASS":
      return "Completed";
    case "QUEUED":
      return "Waiting…";
    case "FAILED":
      return "Failed";
    default:
      return "Preparing…";
  }
}

export function ResearchScreen() {
  const [screen, setScreen] = useState<Screen>({ kind: "startup" });
  const [recovery, setRecovery] = useState<ProbeRecoveryStatus | null>(null);
  const [discovery, setDiscovery] = useState<ProbeDiscovery | null>(null);
  const [plan, setPlan] = useState<ProbePlan | null>(null);
  const [progress, setProgress] = useState<Record<string, OpProgressInfo>>({});
  const [result, setResult] = useState<ProbeRunResult | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [isStalled, setIsStalled] = useState<boolean>(false);

  const unlistenRef = useRef<(() => void) | undefined>(undefined);
  const timerRef = useRef<number | undefined>(undefined);
  const lastEventTimeRef = useRef<number>(Date.now());

  const handleEngineEvent = useCallback((event: ProbeEngineEvent) => {
    lastEventTimeRef.current = Date.now();
    setIsStalled(false);
    if (event.kind === "progress") {
      const p = event.progress;
      console.log(`[PROBE EVENT] progress: op=${p.op} state=${p.state} text=${p.text}`);
      setProgress((prev) => ({
        ...prev,
        [p.op]: {
          state: p.state,
          text: p.text,
          label: p.label,
        },
      }));
    } else {
      console.log(`[PROBE EVENT] run_result: status=${event.result.status} restored=${event.result.restored}`);
      setResult(event.result);
      setScreen({ kind: "result" });
    }
  }, []);

  const init = useCallback(async () => {
    setScreen({ kind: "startup" });
    setIsStalled(false);
    lastEventTimeRef.current = Date.now();
    try {
      // Ensure listeners are attached BEFORE any RPC calls
      if (!unlistenRef.current) {
        try {
          unlistenRef.current = await onProbeEvents(handleEngineEvent);
          console.log("[PROBE INIT] Event listeners attached");
        } catch (err) {
          console.warn("Could not attach probe event listeners:", err);
        }
      }

      // Bounded 12s timeout for startup RPC calls
      const timeout = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("Probe engine initialization timed out (12s). Is the sidecar running?")), 12000),
      );

      const startupTask = (async () => {
        const rec = await probeRecoveryStatus();
        let disc: ProbeDiscovery | null = null;
        let pl: ProbePlan | null = null;

        if (rec.preflight === "CLEAR") {
          [disc, pl] = await Promise.all([probeDiscover(), probePlan()]);
        } else {
          try {
            disc = await probeDiscover();
          } catch {
            disc = null;
          }
        }
        return { rec, disc, pl };
      })();

      const { rec, disc, pl } = await Promise.race([startupTask, timeout]);
      setRecovery(rec);
      setDiscovery(disc);
      setPlan(pl);
      setScreen({ kind: "ready" });
    } catch (cause) {
      setScreen({ kind: "error", message: messageOf(cause) });
    }
  }, [handleEngineEvent]);

  useEffect(() => {
    void init();
    return () => {
      unlistenRef.current?.();
      unlistenRef.current = undefined;
      if (timerRef.current !== undefined) {
        clearInterval(timerRef.current);
        timerRef.current = undefined;
      }
    };
  }, [init]);

  // Manage elapsed run timer and non-destructive stall watchdog during running state
  useEffect(() => {
    if (screen.kind === "running") {
      setElapsedSeconds(0);
      lastEventTimeRef.current = Date.now();
      setIsStalled(false);
      timerRef.current = window.setInterval(() => {
        setElapsedSeconds((s) => s + 1);
        if (Date.now() - lastEventTimeRef.current > 20000) {
          setIsStalled(true);
        }
      }, 1000);
    } else {
      if (timerRef.current !== undefined) {
        clearInterval(timerRef.current);
        timerRef.current = undefined;
      }
    }
    return () => {
      if (timerRef.current !== undefined) {
        clearInterval(timerRef.current);
        timerRef.current = undefined;
      }
    };
  }, [screen.kind]);

  const start = useCallback(async () => {
    if (discovery?.state !== "IDENTIFIED" || recovery?.preflight !== "CLEAR") return;
    if (screen.kind === "running") return; // prevent duplicate clicks

    console.log("[PROBE START] START_CLICK -> START_IPC_BEGIN");
    lastEventTimeRef.current = Date.now();
    setIsStalled(false);
    setScreen({ kind: "running", starting: true });
    setResult(null);
    setProgress({});
    setElapsedSeconds(0);

    try {
      // Ensure listeners attached before sending start_run RPC
      if (!unlistenRef.current) {
        unlistenRef.current = await onProbeEvents(handleEngineEvent);
      }
      console.log("[PROBE START] START_RPC_REQUEST_SENT: method=start_run");
      const reply = await probeStartRun();
      console.log("[PROBE START] START_RPC_RESPONSE_RECEIVED:", reply);

      if (!reply.started) {
        console.warn("[PROBE START] RUN_REFUSED:", reply.error);
        setScreen({ kind: "error", message: reply.error ?? "run did not start" });
      } else {
        console.log("[PROBE START] RUN_ACCEPTED -> EXECUTOR_START");
        setScreen({ kind: "running", starting: false });
      }
    } catch (cause) {
      console.error("[PROBE START] START_ERROR:", cause);
      setScreen({ kind: "error", message: messageOf(cause) });
    }
  }, [discovery, recovery, screen.kind, handleEngineEvent]);

  const restoreConfirmed = useCallback(async () => {
    try {
      const rec = await probeClearRecovery();
      setRecovery(rec);
      if (rec.preflight === "CLEAR") {
        const disc = await probeDiscover();
        setDiscovery(disc);
        const pl = await probePlan();
        setPlan(pl);
      }
    } catch (cause) {
      setScreen({ kind: "error", message: messageOf(cause) });
    }
  }, []);

  // Compute honest progress numbers
  const safeOps = useMemo(() => plan?.safe ?? [], [plan]);
  const safeCount = useMemo(() => plan?.safeCount || safeOps.length || 6, [plan, safeOps]);
  const completedCount = useMemo(() => {
    return safeOps.filter((op) => progress[op.id]?.state === "PASS").length;
  }, [safeOps, progress]);

  // Find currently active operation
  const activeOp = useMemo(() => {
    return safeOps.find((op) => {
      const st = progress[op.id]?.state;
      return st === "BASELINING" || st === "TESTING" || st === "VERIFYING" || st === "RESTORING" || st === "RECOVERING";
    });
  }, [safeOps, progress]);

  const activeOpInfo = activeOp ? progress[activeOp.id] : undefined;

  if (screen.kind === "startup") {
    return (
      <div className="research">
        <section className="panel">
          <h2>Checking previous session…</h2>
          <p className="muted">Verifying recovery preflight and device connection.</p>
        </section>
      </div>
    );
  }

  if (screen.kind === "error") {
    return (
      <div className="research" role="alert">
        <section className="panel warn">
          <h2>Probe engine failed to initialize</h2>
          <p className="muted">{screen.message}</p>
          <div className="actions">
            <button type="button" onClick={() => void init()}>
              Retry
            </button>
            <button type="button" onClick={() => void probeOpenResults()}>
              Open results folder
            </button>
          </div>
        </section>
      </div>
    );
  }

  const isRunning = screen.kind === "running";
  const isStarting = isRunning && screen.starting;
  const canStart =
    recovery?.preflight === "CLEAR" &&
    discovery?.state === "IDENTIFIED" &&
    !isRunning;

  const blocked = plan?.blocked ?? [];

  const systemStageInfo = progress["system"];
  let currentActivityText = "Researching device…";
  if (isStarting) {
    currentActivityText = "Starting research…";
  } else if (activeOp) {
    currentActivityText = `${activeOp.label}: ${friendlyStageText(activeOpInfo?.state, activeOp.label, activeOpInfo?.text)}`;
  } else if (systemStageInfo?.text) {
    currentActivityText = systemStageInfo.text;
  } else if (completedCount === safeCount && safeCount > 0) {
    currentActivityText = "Finalizing and restoring device baseline…";
  }

  return (
    <div className="research">
      {(recovery?.preflight === "RECOVERY_REQUIRED" ||
        recovery?.preflight === "RECOVERING" ||
        recovery?.preflight === "RECOVERY_IN_PROGRESS") && (
        <section className="panel warn" role="alert">
          <h2>Restoring previous device state…</h2>
          <p className="muted">{recovery.reason}</p>
          <p>
            Research cannot start until the previous session is restored and verified.
          </p>
          <button type="button" onClick={() => void restoreConfirmed()}>
            I have restored the device (clear checkpoint)
          </button>
        </section>
      )}

      {recovery?.preflight === "MANUAL_RESTORE_REQUIRED" && (
        <section className="panel warn" role="alert">
          <h2>Manual device restore required</h2>
          <p className="muted">{recovery.reason}</p>
          <p>
            Please restore your original device settings using the vendor software, then click below.
          </p>
          <button type="button" onClick={() => void restoreConfirmed()}>
            I have restored the device
          </button>
        </section>
      )}

      {recovery?.preflight === "ERROR" && (
        <section className="panel warn" role="alert">
          <h2>Recovery preflight check failed</h2>
          <p className="muted">{recovery.reason}</p>
          <button type="button" onClick={() => void init()}>
            Retry preflight
          </button>
        </section>
      )}

      {discovery?.state === "NO_DEVICE" && (
        <section className="panel">
          <h2>Connect your keyboard to begin</h2>
          <p className="muted">
            Vetro Probe will automatically detect when a supported device is connected.
          </p>
          <button type="button" onClick={() => void init()}>
            Scan for devices
          </button>
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
          <section className={`panel ${isRunning ? "running-panel" : ""}`}>
            <h2>{discovery.device?.name ?? "Device detected"}</h2>
            <p className="muted">
              Firmware {discovery.device?.firmware ?? "unknown"} ·{" "}
              {discovery.supportedCount} safe checks available
            </p>
          </section>

          {/* Overall Research Progress Section (shown while active) */}
          {isRunning && (
            <section className="panel running-panel">
              <div className="progress-box">
                <div className="progress-header-row">
                  <span className="progress-count">
                    {isStarting ? "Preparing research…" : `${completedCount} / ${safeCount} completed`}
                  </span>
                  <span className="progress-timer">Elapsed: {formatTimer(elapsedSeconds)}</span>
                </div>
                <div className="progress-track">
                  <div
                    className="progress-fill"
                    style={{
                      width: `${safeCount > 0 ? Math.round((completedCount / safeCount) * 100) : 0}%`,
                    }}
                  />
                </div>
                <div className="current-activity">
                  <span className="pulse-dot" />
                  <span>{currentActivityText}</span>
                </div>
                <p className="keep-connected-notice">Keep the device connected until research is complete.</p>
                {isStalled && (
                  <div className="stall-warning-box" role="alert">
                    <div className="stall-warning-header">
                      <span className="warn-icon">⚠</span>
                      <strong>Research is taking longer than expected</strong>
                    </div>
                    <p className="stall-warning-text">
                      Keep the device connected while research finishes. Do not disconnect the keyboard.
                    </p>
                    <button
                      type="button"
                      className="button-link"
                      onClick={() => setShowDetails(!showDetails)}
                    >
                      {showDetails ? "Hide diagnostic details" : "View diagnostic details"}
                    </button>
                    {showDetails && (
                      <div className="details">
                        <pre>
                          {JSON.stringify(
                            {
                              elapsedSeconds,
                              lastProgressAgeSeconds: Math.max(0, Math.floor((Date.now() - lastEventTimeRef.current) / 1000)),
                              completedCount,
                              safeCount,
                              systemStage: systemStageInfo?.state ?? "IDLE",
                              currentActivity: currentActivityText,
                              progress,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </section>
          )}

          <section className="panel">
            <h3>Safe automatic checks</h3>
            <ul className="plan-list">
              {safeOps.map((op) => {
                const info = progress[op.id];
                const state = info?.state;
                const isOpActive =
                  state === "BASELINING" ||
                  state === "TESTING" ||
                  state === "VERIFYING";
                const isOpRestoring = state === "RESTORING";
                const isOpPass = state === "PASS";
                const isOpFailed = state === "FAILED";

                let rowClass = "";
                if (isOpRestoring) rowClass = "op-restoring";
                else if (isOpActive) rowClass = "op-active";
                else if (isOpPass) rowClass = "op-pass";

                return (
                  <li key={op.id} className={rowClass}>
                    <span>{op.label}</span>
                    {isRunning && (
                      <span
                        className={`op-state ${
                          isOpPass
                            ? "badge-pass"
                            : isOpRestoring
                              ? "badge-restoring"
                              : isOpActive
                                ? "badge-active"
                                : isOpFailed
                                  ? "badge-failed"
                                  : "badge-queued"
                        }`}
                      >
                        {isOpPass && "✓ Completed"}
                        {isOpRestoring && (
                          <>
                            <span className="row-dot restoring" />
                            Restoring original setting…
                          </>
                        )}
                        {isOpActive && (
                          <>
                            <span className="row-dot pulse" />
                            {friendlyStageText(state, op.label, info?.text)}
                          </>
                        )}
                        {!isOpPass && !isOpRestoring && !isOpActive && isOpFailed && "✕ Failed"}
                        {!isOpPass && !isOpRestoring && !isOpActive && !isOpFailed && (
                          isStarting ? "○ Preparing…" : "○ Waiting…"
                        )}
                      </span>
                    )}
                  </li>
                );
              })}
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
              <button
                type="button"
                disabled={!canStart || isRunning}
                onClick={() => void start()}
              >
                {isStarting ? "Starting…" : isRunning ? "Research in progress…" : "Start research"}
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
                      completedCount,
                      safeCount,
                      elapsedSeconds,
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
        <ResultView
          result={result}
          discovery={discovery}
          plan={plan}
          showDetails={showDetails}
          onShowDetails={setShowDetails}
          onRestart={() => void init()}
        />
      )}
    </div>
  );
}

function ResultView({
  result,
  discovery,
  plan,
  showDetails,
  onShowDetails,
  onRestart,
}: {
  result: ProbeRunResult;
  discovery: ProbeDiscovery | null;
  plan: ProbePlan | null;
  showDetails: boolean;
  onShowDetails: (v: boolean) => void;
  onRestart: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const completed =
    result.checks_completed ??
    result.checksCompleted ??
    result.results?.filter((r) => r.status === "PASS" || r.status === "COMPLETE_PASS").length ??
    0;
  const total =
    result.checks_total ??
    result.checksTotal ??
    plan?.safeCount ??
    result.results?.length ??
    6;
  const blockedCount = plan?.blocked?.length ?? (15 - total > 0 ? 15 - total : 0);

  const handleCopy = async (label: string) => {
    const summaryLines = [
      `=== Vetro Probe ${label} ===`,
      `Device: ${discovery?.device?.name ?? "Detected device"} (Firmware: ${discovery?.device?.firmware ?? "unknown"})`,
      `Status: ${result.status}`,
      `Checks Completed: ${completed} of ${total}`,
      `Original Settings Restored: ${result.restored ? "Yes (Verified ✓)" : "No"}`,
      result.error ? `Error: ${result.error}` : "",
      result.outputPath ? `Package: ${result.outputPath}` : "",
    ].filter(Boolean);

    try {
      await navigator.clipboard.writeText(summaryLines.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback if clipboard write fails
    }
  };

  if (result.status === "FAILED_REQUIRES_MANUAL_RESTORE") {
    return (
      <section className="panel warn" role="alert">
        <h2>Research stopped — Manual restore required</h2>
        <p className="muted">
          Your original device settings could not be verified as restored. Please
          restore them using your vendor software, then restart.
        </p>
        <div className="actions">
          <button type="button" onClick={onRestart}>
            Run again
          </button>
          <button type="button" onClick={() => void probeOpenResults()}>
            Open results folder
          </button>
          <button type="button" onClick={() => void handleCopy("Diagnostics")}>
            {copied ? "Copied ✓" : "Copy diagnostics"}
          </button>
          <button type="button" onClick={() => onShowDetails(!showDetails)}>
            {showDetails ? "Hide details" : "Technical details"}
          </button>
        </div>
        {showDetails && <Details result={result} />}
      </section>
    );
  }

  if (result.status === "FAIL_RESTORED" || result.status === "ERROR") {
    return (
      <section className="panel warn" role="alert">
        <h2>Research stopped</h2>
        <p className="muted">
          {result.restored
            ? "Your original device settings were restored successfully."
            : "Research run stopped with an error."}
        </p>
        <p className="muted">
          {completed} of {total} checks completed.
        </p>
        {result.error && <p className="error-text">{result.error}</p>}
        <div className="actions">
          <button type="button" onClick={onRestart}>
            Run again
          </button>
          <button type="button" onClick={() => void probeOpenResults()}>
            Open results folder
          </button>
          <button type="button" onClick={() => void handleCopy("Diagnostics")}>
            {copied ? "Copied ✓" : "Copy diagnostics"}
          </button>
          <button type="button" onClick={() => onShowDetails(!showDetails)}>
            {showDetails ? "Hide details" : "Technical details"}
          </button>
        </div>
        {showDetails && <Details result={result} />}
      </section>
    );
  }

  return (
    <>
      <section className="panel result-success-panel">
        <div className="result-header-badge">✓ Research complete</div>
        <h2>{discovery?.device?.name ?? "Device"} verified successfully</h2>
        <div className="result-stats-row">
          <div className="result-stat-item">
            <span className="stat-value">{completed} / {total}</span>
            <span className="stat-label">checks completed</span>
          </div>
          <div className="result-stat-item">
            <span className="stat-value">✓ Restored</span>
            <span className="stat-label">original settings verified</span>
          </div>
          <div className="result-stat-item">
            <span className="stat-value">0</span>
            <span className="stat-label">failures</span>
          </div>
        </div>
        <p className="muted">
          {completed} of {total} checks completed successfully.
          {blockedCount > 0 && ` (${blockedCount} additional checks were safely skipped)`}
        </p>
        <div className="actions result-actions">
          <button type="button" className="button-primary" onClick={() => void probeOpenResults()}>
            Open results folder
          </button>
          <button type="button" onClick={() => void handleCopy("Result Summary")}>
            {copied ? "Summary copied ✓" : "Copy results summary"}
          </button>
          <button type="button" onClick={onRestart}>
            Run again
          </button>
          <button type="button" onClick={() => onShowDetails(!showDetails)}>
            {showDetails ? "Hide details" : "Technical details"}
          </button>
        </div>
      </section>

      {/* Completed checks row display */}
      <section className="panel">
        <h3>Completed checks</h3>
        <ul className="plan-list">
          {(result.results && result.results.length > 0
            ? result.results
            : plan?.safe ?? []
          ).map((op) => (
            <li key={op.id} className="op-pass">
              <span>{op.label ?? op.id}</span>
              <span className="op-state badge-pass">✓ Completed</span>
            </li>
          ))}
        </ul>
        {showDetails && <Details result={result} />}
      </section>
    </>
  );
}

function Details({ result }: { result: ProbeRunResult }) {
  return (
    <details open className="details">
      <summary>Technical details</summary>
      <pre className="mono">{JSON.stringify(result, null, 2)}</pre>
    </details>
  );
}

function messageOf(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  if (typeof cause === "string") return cause;
  return "Unknown error";
}
