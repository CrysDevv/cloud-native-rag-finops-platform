import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API_LOCAL = "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: API_LOCAL, changeOrigin: true },
      "/health": { target: API_LOCAL, changeOrigin: true },
    },
  },
  build: { sourcemap: false, target: "es2022" },
});
