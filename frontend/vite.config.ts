/** Vite development-server and Vitest configuration for the Favlist client. */
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

/** Build configuration with a localhost backend default for non-Docker development. */
export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "VITE_");
  const apiTarget = environment.VITE_API_PROXY_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    server: { proxy: { "/api": { target: apiTarget, changeOrigin: true } } },
    test: { environment: "jsdom", setupFiles: "./src/test-setup.ts", css: true },
  };
});
