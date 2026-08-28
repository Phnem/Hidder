import { useEffect, useState } from "react";
import { ResearchScreen } from "../../app/src/screens/Research";
import { probeSetMode } from "../../app/src/ipc";

export function StandaloneApp() {
  const [version] = useState("0.3.0");

  useEffect(() => {
    // If started with ?mode=demo, configure demo scenario
    if (window.location.search.includes("demo")) {
      void probeSetMode("demo", "supported");
    }
  }, []);

  return (
    <div className="standalone-container">
      <header className="standalone-header">
        <div className="brand">
          <span className="logo-text">VETRO PROBE</span>
          <span className="version-tag">v{version}</span>
        </div>
      </header>
      <main className="standalone-main">
        <ResearchScreen />
      </main>
    </div>
  );
}
