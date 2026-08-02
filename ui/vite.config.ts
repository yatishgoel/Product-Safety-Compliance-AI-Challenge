import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/static/",
  plugins: [react()],
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/health": "http://localhost:8000",
      "/policy": "http://localhost:8000",
      "/inspect_policy": "http://localhost:8000",
      "/extractors": "http://localhost:8000",
      "/evaluate_product": "http://localhost:8000",
    },
  },
});
