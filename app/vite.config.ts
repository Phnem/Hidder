import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri drives this config: the dev server port is fixed because the Rust side
// is told to load exactly this URL, and a port that silently moves leaves the
// window blank with no error anywhere.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // Rust sources are watched by cargo, not by vite.
      ignored: ["**/src-tauri/**"],
    },
  },
});
