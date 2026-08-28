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

export type Lang = "en" | "ru";

export const translations = {
  en: {
    app_title: "Vetro Probe",
    checking_session: "Checking previous session…",
    checking_desc: "Verifying recovery preflight and device connection.",
    init_failed: "Probe engine failed to initialize",
    retry: "Retry",
    retry_preflight: "Retry preflight",
    open_results_folder: "Open results folder",
    restoring_state: "Restoring previous device state…",
    cannot_start_until_restored: "Research cannot start until the previous session is restored and verified.",
    confirm_restored: "I have restored the device (clear checkpoint)",
    manual_restore_required: "Manual device restore required",
    manual_restore_desc: "Please restore your original device settings using the vendor software, then click below.",
    preflight_failed: "Recovery preflight check failed",
    connect_keyboard: "Connect your keyboard to begin",
    connect_desc: "Vetro Probe will automatically detect when a supported device is connected.",
    scan_devices: "Scan for devices",
    fw_unsupported: "This firmware is not yet supported",
    device_unsupported: "This device is not supported yet",
    device_detected: "Device detected",
    safe_checks_available: "safe checks available",
    preparing_research: "Preparing research…",
    research_in_progress: "Research in progress…",
    completed_count: "completed",
    elapsed: "Elapsed",
    keep_connected: "Keep the device connected until research is complete.",
    stall_warning_title: "Research is taking longer than expected",
    stall_warning_text: "Keep the device connected while research finishes. Do not disconnect the keyboard.",
    show_diagnostics: "View diagnostic details",
    hide_diagnostics: "Hide diagnostic details",
    safe_checks_title: "Safe automatic checks",
    not_available_yet: "Not available yet",
    tester_instructions: "Tester instructions",
    step1: "Connect the device directly by USB if possible.",
    step2: "Close the official vendor configuration software.",
    step3: "Do not disconnect the keyboard while research is running.",
    step4: "Click Start research below.",
    step5: "When complete, click Open results folder and send the generated ZIP.",
    start_research: "Start research",
    starting: "Starting…",
    run_again: "Run again",
    tech_details: "Technical details",
    hide_details: "Hide details",
    research_complete_badge: "✓ Research complete",
    verified_successfully: "verified successfully",
    checks_completed_stat: "checks completed",
    original_restored_stat: "original settings verified",
    failures_stat: "failures",
    of_checks_completed: "checks completed successfully.",
    safely_skipped: "additional checks were safely skipped",
    copy_results_summary: "Copy results summary",
    summary_copied: "Summary copied ✓",
    copy_diagnostics: "Copy diagnostics",
    diag_copied: "Diagnostics copied ✓",
    completed_badge: "✓ Completed",
    failed_badge: "✕ Failed",
    queued_badge: "○ Waiting…",
    preparing_badge: "○ Preparing…",
    restoring_badge: "Restoring original setting…",
    stopped_manual_title: "Research stopped — Manual restore required",
    stopped_manual_text: "Your original device settings could not be verified as restored. Please restore them using your vendor software, then restart.",
    stopped_title: "Research stopped",
    stopped_restored_text: "Your original device settings were restored successfully.",
    stopped_error_text: "Research run stopped with an error.",
    report_issue_btn: "Report compatibility problem",
    calm_failure_desc: "Compatibility check could not be completed. Your previous settings were restored where possible. You can send the diagnostic report so we can add support for this device.",
  },
  ru: {
    app_title: "Vetro Probe",
    checking_session: "Проверка предыдущей сессии…",
    checking_desc: "Проверка готовности к восстановлению и подключения устройства.",
    init_failed: "Не удалось инициализировать движок Probe",
    retry: "Повторить",
    retry_preflight: "Повторить проверку",
    open_results_folder: "Открыть папку с результатами",
    restoring_state: "Восстановление состояния устройства…",
    cannot_start_until_restored: "Исследование не может начаться, пока не проверен возврат исходного состояния.",
    confirm_restored: "Я восстановил устройство (сбросить контрольную точку)",
    manual_restore_required: "Требуется ручное восстановление устройства",
    manual_restore_desc: "Пожалуйста, восстановите исходные параметры через фирменное ПО производителя, затем нажмите кнопку ниже.",
    preflight_failed: "Ошибка предварительной проверки",
    connect_keyboard: "Подключите клавиатуру для начала",
    connect_desc: "Vetro Probe автоматически обнаружит поддерживаемое устройство при подключении.",
    scan_devices: "Поиск устройств",
    fw_unsupported: "Данная версия прошивки пока не поддерживается",
    device_unsupported: "Данное устройство пока не поддерживается",
    device_detected: "Устройство обнаружено",
    safe_checks_available: "безопасных проверок доступно",
    preparing_research: "Подготовка к исследованию…",
    research_in_progress: "Идёт исследование…",
    completed_count: "выполнено",
    elapsed: "Прошло времени",
    keep_connected: "Не отключайте устройство до полного завершения исследования.",
    stall_warning_title: "Исследование выполняется дольше обычного",
    stall_warning_text: "Не отключайте клавиатуру. Исследование продолжает безопасно выполняться в фоне.",
    show_diagnostics: "Показать диагностические данные",
    hide_diagnostics: "Скрыть диагностику",
    safe_checks_title: "Безопасные автоматические проверки",
    not_available_yet: "Пока недоступно",
    tester_instructions: "Инструкция для тестировщика",
    step1: "Подключите устройство напрямую по USB (по проводу), если возможно.",
    step2: "Закройте официальное ПО производителя (AULA / др. софт).",
    step3: "Не отключайте клавиатуру во время выполнения исследования.",
    step4: "Нажмите кнопку «Начать исследование» ниже.",
    step5: "По завершении нажмите «Открыть папку с результатами» и отправьте созданный ZIP-архив.",
    start_research: "Начать исследование",
    starting: "Запуск…",
    run_again: "Запустить снова",
    tech_details: "Технические детали",
    hide_details: "Скрыть детали",
    research_complete_badge: "✓ Исследование завершено",
    verified_successfully: "успешно проверено",
    checks_completed_stat: "проверок выполнено",
    original_restored_stat: "исходные настройки подтверждены",
    failures_stat: "ошибок",
    of_checks_completed: "проверок успешно завершено.",
    safely_skipped: "дополнительных проверок безопасно пропущено",
    copy_results_summary: "Скопировать краткий отчёт",
    summary_copied: "Отчёт скопирован ✓",
    copy_diagnostics: "Скопировать диагностику",
    diag_copied: "Диагностика скопирована ✓",
    completed_badge: "✓ Завершено",
    failed_badge: "✕ Ошибка",
    queued_badge: "○ Ожидание…",
    preparing_badge: "○ Подготовка…",
    restoring_badge: "Восстановление исходных настроек…",
    stopped_manual_title: "Исследование остановлено — требуется ручное восстановление",
    stopped_manual_text: "Не удалось автоматически подтвердить возврат исходных настроек. Восстановите их через ПО производителя и перезапустите Probe.",
    stopped_title: "Исследование остановлено",
    stopped_restored_text: "Исходные настройки устройства были успешно восстановлены.",
    stopped_error_text: "Исследование завершилось с ошибкой.",
    report_issue_btn: "Сообщить о проблеме на GitHub",
    calm_failure_desc: "Проверка совместимости не была завершена. Ваши исходные настройки были восстановлены. Вы можете отправить диагностический отчет, чтобы мы добавили поддержку этого устройства.",
  },
};

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

function friendlyStageText(state?: ProbeProgressState, label?: string, text?: string, lang: Lang = "en"): string {
  if (text && text.trim().length > 0 && text !== "Waiting...") return text;
  const t = translations[lang];
  switch (state) {
    case "BASELINING":
      return lang === "ru" ? `Считывание исходных настроек…` : `Reading ${label ?? "original"} setting…`;
    case "TESTING":
      return lang === "ru" ? `Проверка временных значений…` : `Testing temporary setting…`;
    case "VERIFYING":
      return lang === "ru" ? `Проверка результата…` : `Verifying result…`;
    case "RESTORING":
      return t.restoring_badge;
    case "RECOVERING":
      return lang === "ru" ? `Восстановление состояния…` : `Recovering original device state…`;
    case "PASS":
      return t.completed_badge;
    case "QUEUED":
      return t.queued_badge;
    case "FAILED":
      return t.failed_badge;
    default:
      return t.preparing_badge;
  }
}

export function ResearchScreen() {
  const [lang, setLangState] = useState<Lang>(() => {
    try {
      const saved = localStorage.getItem("vetro_probe_lang");
      if (saved === "ru" || saved === "en") return saved;
      if (typeof navigator !== "undefined" && navigator.language.startsWith("ru")) return "ru";
    } catch {}
    return "en";
  });

  const setLang = (newLang: Lang) => {
    setLangState(newLang);
    try {
      localStorage.setItem("vetro_probe_lang", newLang);
    } catch {}
  };

  const t = translations[lang];

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
      if (!unlistenRef.current) {
        try {
          unlistenRef.current = await onProbeEvents(handleEngineEvent);
          console.log("[PROBE INIT] Event listeners attached");
        } catch (err) {
          console.warn("Could not attach probe event listeners:", err);
        }
      }

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
      console.log("[PROBE INIT] Startup complete:", { rec, disc, pl });
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
    if (screen.kind === "running") return;

    console.log("[PROBE START] START_CLICK -> START_IPC_BEGIN");
    lastEventTimeRef.current = Date.now();
    setIsStalled(false);
    setScreen({ kind: "running", starting: true });
    setResult(null);
    setProgress({});
    setElapsedSeconds(0);

    try {
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

  const safeOps = useMemo(() => plan?.safe ?? [], [plan]);
  const safeCount = useMemo(() => plan?.safeCount || safeOps.length || 6, [plan, safeOps]);
  const completedCount = useMemo(() => {
    return safeOps.filter((op) => progress[op.id]?.state === "PASS").length;
  }, [safeOps, progress]);

  const activeOp = useMemo(() => {
    return safeOps.find((op) => {
      const st = progress[op.id]?.state;
      return st === "BASELINING" || st === "TESTING" || st === "VERIFYING" || st === "RESTORING" || st === "RECOVERING";
    });
  }, [safeOps, progress]);

  const activeOpInfo = activeOp ? progress[activeOp.id] : undefined;

  const renderTopBar = () => (
    <div className="probe-top-bar">
      <div className="probe-app-title">
        {t.app_title}
        <span className="version-tag">v0.3.0</span>
      </div>
      <div className="lang-switcher">
        <button
          type="button"
          className={lang === "en" ? "active" : ""}
          onClick={() => setLang("en")}
        >
          EN
        </button>
        <button
          type="button"
          className={lang === "ru" ? "active" : ""}
          onClick={() => setLang("ru")}
        >
          RU
        </button>
      </div>
    </div>
  );

  if (screen.kind === "startup") {
    return (
      <div className="research">
        {renderTopBar()}
        <section className="panel">
          <h2>{t.checking_session}</h2>
          <p className="muted">{t.checking_desc}</p>
        </section>
      </div>
    );
  }

  if (screen.kind === "error") {
    return (
      <div className="research" role="alert">
        {renderTopBar()}
        <section className="panel warn">
          <h2>{t.init_failed}</h2>
          <p className="muted">{screen.message}</p>
          <div className="actions">
            <button type="button" onClick={() => void init()}>
              {t.retry}
            </button>
            <button type="button" onClick={() => void probeOpenResults()}>
              {t.open_results_folder}
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
  let currentActivityText = lang === "ru" ? "Исследование устройства…" : "Researching device…";
  if (isStarting) {
    currentActivityText = lang === "ru" ? "Запуск исследования…" : "Starting research…";
  } else if (activeOp) {
    currentActivityText = `${activeOp.label}: ${friendlyStageText(activeOpInfo?.state, activeOp.label, activeOpInfo?.text, lang)}`;
  } else if (systemStageInfo?.text) {
    currentActivityText = systemStageInfo.text;
  } else if (completedCount === safeCount && safeCount > 0) {
    currentActivityText = lang === "ru" ? "Финализация и восстановление параметров…" : "Finalizing and restoring device baseline…";
  }

  return (
    <div className="research">
      {renderTopBar()}

      {(recovery?.preflight === "RECOVERY_REQUIRED" ||
        recovery?.preflight === "RECOVERING" ||
        recovery?.preflight === "RECOVERY_IN_PROGRESS") && (
        <section className="panel warn" role="alert">
          <h2>{t.restoring_state}</h2>
          <p className="muted">{recovery.reason}</p>
          <p>{t.cannot_start_until_restored}</p>
          <button type="button" onClick={() => void restoreConfirmed()}>
            {t.confirm_restored}
          </button>
        </section>
      )}

      {recovery?.preflight === "MANUAL_RESTORE_REQUIRED" && (
        <section className="panel warn" role="alert">
          <h2>{t.manual_restore_required}</h2>
          <p className="muted">{recovery.reason}</p>
          <p>{t.manual_restore_desc}</p>
          <button type="button" onClick={() => void restoreConfirmed()}>
            {t.confirm_restored}
          </button>
        </section>
      )}

      {recovery?.preflight === "ERROR" && (
        <section className="panel warn" role="alert">
          <h2>{t.preflight_failed}</h2>
          <p className="muted">{recovery.reason}</p>
          <button type="button" onClick={() => void init()}>
            {t.retry_preflight}
          </button>
        </section>
      )}

      {discovery?.state === "NO_DEVICE" && (
        <section className="panel">
          <h2>{t.connect_keyboard}</h2>
          <p className="muted">{t.connect_desc}</p>

          <div className="tester-instructions" style={{ marginTop: "1rem" }}>
            <h4>{lang === "ru" ? "Чек-лист для подключения:" : "Connection checklist:"}</h4>
            <ol>
              <li>
                {lang === "ru"
                  ? "Подключите клавиатуру по кабелю USB напрямую к ПК (не через 2.4G адаптер и не по Bluetooth)."
                  : "Connect the keyboard directly via USB cable (not 2.4G wireless dongle or Bluetooth)."}
              </li>
              <li>
                {lang === "ru"
                  ? "Переведите физический переключатель режимов на корпусе клавиатуры в положение Cable (USB)."
                  : "Set the physical mode switch on the keyboard to Cable / USB mode."}
              </li>
              <li>
                {lang === "ru"
                  ? "Полностью закройте официальное приложение AULA / производителя (включая иконку в трее Windows)."
                  : "Close the official vendor configuration software (AULA app) completely (including Windows tray)."}
              </li>
              <li>
                {lang === "ru"
                  ? "Поддерживаемая модель в данном пилоте: AULA HERO 84 HE (0x372E:0x103E)."
                  : "Target device for this pilot: AULA HERO 84 HE (0x372E:0x103E)."}
              </li>
            </ol>
          </div>

          {discovery.reason && (
            <p className="muted" style={{ marginTop: "0.75rem", fontSize: "0.85rem" }}>
              <strong>{lang === "ru" ? "Статус сканирования:" : "Scan status:"}</strong> {discovery.reason}
            </p>
          )}

          {((discovery.detected_devices && discovery.detected_devices.length > 0) ||
            (discovery.detectedDevices && discovery.detectedDevices.length > 0)) && (
            <details className="details" style={{ marginTop: "0.75rem" }}>
              <summary>
                {lang === "ru"
                  ? `Обнаруженные USB HID-устройства (${(discovery.detected_devices || discovery.detectedDevices)!.length} шт.)`
                  : `Detected USB HID devices (${(discovery.detected_devices || discovery.detectedDevices)!.length})`}
              </summary>
              <ul className="plan-list" style={{ marginTop: "0.5rem" }}>
                {(discovery.detected_devices || discovery.detectedDevices)!.map((d, i) => (
                  <li key={i}>
                    <span>{d.name || "USB Device"} {d.manufacturer ? `(${d.manufacturer})` : ""}</span>
                    <span className="mono" style={{ fontSize: "0.8rem", color: "var(--accent, #febe10)" }}>
                      {d.vid}:{d.pid}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="actions" style={{ marginTop: "1rem" }}>
            <button type="button" onClick={() => void init()}>
              {t.scan_devices}
            </button>
          </div>
        </section>
      )}

      {discovery?.state === "FW_UNSUPPORTED" && (
        <section className="panel">
          <h2>{t.fw_unsupported}</h2>
          <p className="muted">{discovery.reason}</p>
        </section>
      )}

      {(discovery?.state === "IDENTITY_MISMATCH" || discovery?.state === "UNSUPPORTED") && (
        <section className="panel">
          <h2>{t.device_unsupported}</h2>
          <p className="muted">{discovery.reason}</p>
        </section>
      )}

      {discovery?.state === "IDENTIFIED" && (
        <>
          <section className={`panel ${isRunning ? "running-panel" : ""}`}>
            <h2>{discovery.device?.name ?? t.device_detected}</h2>
            <p className="muted">
              Firmware {discovery.device?.firmware ?? "unknown"} ·{" "}
              {discovery.supportedCount} {t.safe_checks_available}
            </p>
          </section>

          {isRunning && (
            <section className="panel running-panel">
              <div className="progress-box">
                <div className="progress-header-row">
                  <span className="progress-count">
                    {isStarting
                      ? t.preparing_research
                      : `${completedCount} / ${safeCount} ${t.completed_count}`}
                  </span>
                  <span className="progress-timer">
                    {t.elapsed}: {formatTimer(elapsedSeconds)}
                  </span>
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
                <p className="keep-connected-notice">{t.keep_connected}</p>
                {isStalled && (
                  <div className="stall-warning-box" role="alert">
                    <div className="stall-warning-header">
                      <span className="warn-icon">⚠</span>
                      <strong>{t.stall_warning_title}</strong>
                    </div>
                    <p className="stall-warning-text">{t.stall_warning_text}</p>
                    <button
                      type="button"
                      className="button-link"
                      onClick={() => setShowDetails(!showDetails)}
                    >
                      {showDetails ? t.hide_diagnostics : t.show_diagnostics}
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
            <h3>{t.safe_checks_title}</h3>
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
                        {isOpPass && t.completed_badge}
                        {isOpRestoring && (
                          <>
                            <span className="row-dot restoring" />
                            {t.restoring_badge}
                          </>
                        )}
                        {isOpActive && (
                          <>
                            <span className="row-dot pulse" />
                            {friendlyStageText(state, op.label, info?.text, lang)}
                          </>
                        )}
                        {!isOpPass && !isOpRestoring && !isOpActive && isOpFailed && t.failed_badge}
                        {!isOpPass && !isOpRestoring && !isOpActive && !isOpFailed && (
                          isStarting ? t.preparing_badge : t.queued_badge
                        )}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>

            {blocked.length > 0 && (
              <>
                <h3>{t.not_available_yet}</h3>
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

            {!isRunning && (
              <div className="tester-instructions">
                <h4>{t.tester_instructions}</h4>
                <ol>
                  <li>{t.step1}</li>
                  <li>{t.step2}</li>
                  <li>{t.step3}</li>
                  <li>{t.step4}</li>
                  <li>{t.step5}</li>
                </ol>
              </div>
            )}

            <div className="actions">
              <button
                type="button"
                disabled={!canStart || isRunning}
                onClick={() => void start()}
              >
                {isStarting ? t.starting : isRunning ? t.research_in_progress : t.start_research}
              </button>
              <button type="button" onClick={() => setShowDetails((v) => !v)}>
                {showDetails ? t.hide_details : t.tech_details}
              </button>
            </div>

            {showDetails && (
              <details open className="details">
                <summary>{t.tech_details}</summary>
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
          lang={lang}
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
  lang,
  showDetails,
  onShowDetails,
  onRestart,
}: {
  result: ProbeRunResult;
  discovery: ProbeDiscovery | null;
  plan: ProbePlan | null;
  lang: Lang;
  showDetails: boolean;
  onShowDetails: (v: boolean) => void;
  onRestart: () => void;
}) {
  const t = translations[lang];
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
      `Vetro Probe v0.3.0 ${label}:`,
      `Device: ${discovery?.device?.name ?? "Detected device"} (FW ${discovery?.device?.firmware ?? "unknown"})`,
      `Status: ${result.status} (${completed}/${total} passed, 0 failed${blockedCount > 0 ? `, ${blockedCount} safely skipped` : ""})`,
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
        <h2>{t.stopped_manual_title}</h2>
        <p className="muted">{t.stopped_manual_text}</p>
        <p style={{ marginTop: "0.5rem", fontSize: "0.9rem" }}>{t.calm_failure_desc}</p>
        <div className="actions">
          <button type="button" onClick={onRestart}>
            {t.run_again}
          </button>
          <button type="button" onClick={() => void probeOpenResults()}>
            {t.open_results_folder}
          </button>
          <button
            type="button"
            onClick={() => {
              const url = result.github_issue_url || result.githubIssueUrl || "https://github.com/Phnem/Hidder/issues/new";
              window.open(url, "_blank");
            }}
          >
            {t.report_issue_btn}
          </button>
          <button type="button" onClick={() => void handleCopy("Diagnostics")}>
            {copied ? t.diag_copied : t.copy_diagnostics}
          </button>
          <button type="button" onClick={() => onShowDetails(!showDetails)}>
            {showDetails ? t.hide_details : t.tech_details}
          </button>
        </div>
        {showDetails && <Details result={result} />}
      </section>
    );
  }

  if (result.status === "FAIL_RESTORED" || result.status === "ERROR") {
    return (
      <section className="panel warn" role="alert">
        <h2>{t.stopped_title}</h2>
        <p className="muted">
          {result.restored ? t.stopped_restored_text : t.stopped_error_text}
        </p>
        <p style={{ marginTop: "0.5rem", fontSize: "0.9rem" }}>{t.calm_failure_desc}</p>
        <p className="muted">
          {completed} / {total} {t.checks_completed_stat}.
        </p>
        {result.error && <p className="error-text">{result.error}</p>}
        <div className="actions">
          <button type="button" onClick={onRestart}>
            {t.run_again}
          </button>
          <button type="button" onClick={() => void probeOpenResults()}>
            {t.open_results_folder}
          </button>
          <button
            type="button"
            onClick={() => {
              const url = result.github_issue_url || result.githubIssueUrl || "https://github.com/Phnem/Hidder/issues/new";
              window.open(url, "_blank");
            }}
          >
            {t.report_issue_btn}
          </button>
          <button type="button" onClick={() => void handleCopy("Diagnostics")}>
            {copied ? t.diag_copied : t.copy_diagnostics}
          </button>
          <button type="button" onClick={() => onShowDetails(!showDetails)}>
            {showDetails ? t.hide_details : t.tech_details}
          </button>
        </div>
        {showDetails && <Details result={result} />}
      </section>
    );
  }

  return (
    <>
      <section className="panel result-success-panel">
        <div className="result-header-badge">{t.research_complete_badge}</div>
        <h2>{discovery?.device?.name ?? "Device"} {t.verified_successfully}</h2>
        <div className="result-stats-row">
          <div className="result-stat-item">
            <span className="stat-value">{completed} / {total}</span>
            <span className="stat-label">{t.checks_completed_stat}</span>
          </div>
          <div className="result-stat-item">
            <span className="stat-value">✓ Restored</span>
            <span className="stat-label">{t.original_restored_stat}</span>
          </div>
          <div className="result-stat-item">
            <span className="stat-value">0</span>
            <span className="stat-label">{t.failures_stat}</span>
          </div>
        </div>
        <p className="muted">
          {completed} / {total} {t.of_checks_completed}
          {blockedCount > 0 && ` (${blockedCount} ${t.safely_skipped})`}
        </p>
        <div className="actions result-actions">
          <button type="button" className="button-primary" onClick={() => void probeOpenResults()}>
            {t.open_results_folder}
          </button>
          <button type="button" onClick={() => void handleCopy("Result Summary")}>
            {copied ? t.summary_copied : t.copy_results_summary}
          </button>
          <button type="button" onClick={onRestart}>
            {t.run_again}
          </button>
          <button type="button" onClick={() => onShowDetails(!showDetails)}>
            {showDetails ? t.hide_details : t.tech_details}
          </button>
        </div>
      </section>

      {/* Completed checks row display */}
      <section className="panel">
        <h3>{lang === "ru" ? "Завершённые проверки" : "Completed checks"}</h3>
        <ul className="plan-list">
          {(result.results && result.results.length > 0
            ? result.results
            : plan?.safe ?? []
          ).map((op) => (
            <li key={op.id} className="op-pass">
              <span>{op.label ?? op.id}</span>
              <span className="op-state badge-pass">{t.completed_badge}</span>
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
