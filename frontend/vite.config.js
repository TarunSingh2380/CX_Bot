import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend runs on port 3000; backend is expected on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    open: true,
  },
});
