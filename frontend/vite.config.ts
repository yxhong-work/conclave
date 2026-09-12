import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: ["origin-hollow-morning-sleeve.trycloudflare.com"],
    proxy: {
      "/api": { target: "http://backend:8000", changeOrigin: true },
      "/events": { target: "http://backend:8000", changeOrigin: true, ws: false },
    },
  },
});
