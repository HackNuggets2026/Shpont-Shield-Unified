import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The gateway the dev server proxies /api and /v1 to. Override with SHIELD_GATEWAY=http://host:port.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.SHIELD_GATEWAY || "http://127.0.0.1:8787";
  return {
    // The GitHub Pages preview lives under /<repo>/; set SHIELD_BASE for that build.
    base: env.SHIELD_BASE || "/",
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target, changeOrigin: true },
        "/v1": { target, changeOrigin: true },
      },
    },
    build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  };
});
