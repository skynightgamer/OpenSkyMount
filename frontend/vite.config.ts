import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.OPENSKYMOUNT_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      "/api": backend,
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
  build: {
    outDir: "../backend/openskymount/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  test: { environment: "node" },
});
