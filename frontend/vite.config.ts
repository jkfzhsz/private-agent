/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // base: './' — 打包产物用相对路径引用资源, 否则 Electron file:// 协议
  // 下 /assets/... 会解析到盘符根目录 → 404 → 白屏
  base: "./",
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test-setup.ts"],
  },
});
