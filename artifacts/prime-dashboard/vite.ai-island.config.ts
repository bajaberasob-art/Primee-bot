import path from "path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Self-contained IIFE for the vanilla PRIME AI dashboard (served from dashboard/).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
    dedupe: ["react", "react-dom"],
  },
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: path.resolve(import.meta.dirname, "../../dashboard"),
    emptyOutDir: false,
    copyPublicDir: false,
    cssCodeSplit: false,
    minify: true,
    lib: {
      entry: path.resolve(import.meta.dirname, "src/ai-island/index.tsx"),
      name: "PrimeAIMagicBundle",
      formats: ["iife"],
      fileName: () => "ai-magic-island.js",
    },
  },
});
