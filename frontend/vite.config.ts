import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The .env file lives in the repo root, shared with the backend.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  const apiTarget = process.env.API_PROXY_TARGET || env.API_PROXY_TARGET || "http://localhost:8000";
  return {
    envDir: "..",
    plugins: [react()],
    server: {
      host: true,
      port: 5173,
      proxy: { "/api": { target: apiTarget, changeOrigin: true } },
    },
  };
});
