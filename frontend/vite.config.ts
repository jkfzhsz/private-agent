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
    // 排除构建产物/打包目录里的第三方测试(release2/release3/dist 为
    // electron-builder 输出, 内含上传的第三方源码包测试, 会污染全量扫描)
    exclude: [
      "**/node_modules/**",
      "**/dist/**",
      "**/dist-main/**",
      "**/release2/**",
      "**/release3/**",
      "**/build/**",
    ],
  },
});
