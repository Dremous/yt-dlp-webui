import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物直接输出到后端静态目录 app/static，
// 后端 FastAPI 原样挂载，无需二次拷贝。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/files": "http://localhost:8080",
    },
  },
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
  },
});
