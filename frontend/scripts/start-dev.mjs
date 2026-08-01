// Phase 1 Task 13 - 一键启动脚本(dev 模式)
// 流程: 编译主进程(tsc) → 启动 vite dev server → 等待 5173 就绪 →
//       启动 electron(注入 VITE_DEV_SERVER_URL) → electron 退出时清理 vite
//
// 实现说明: 不依赖 npx/PATH/shell, 直接用 node 执行各 CLI 的 .js 入口,
// electron 直接跑 dist/electron.exe —— 避免 .cmd + shell 拼接的
// DEP0190 警告与 npx 解析问题。
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const isWin = process.platform === "win32";

/** node_modules 下的绝对路径 */
function bin(...segments) {
  return join(root, "node_modules", ...segments);
}

/** 用当前 node 执行 CLI 的 js 入口 */
function runNode(scriptPath, args, opts = {}) {
  return spawn(process.execPath, [scriptPath, ...args], {
    stdio: "inherit",
    ...opts,
  });
}

function waitForPort(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = async () => {
      if (Date.now() > deadline) {
        reject(new Error(`等待 ${url} 就绪超时(${timeoutMs}ms)`));
        return;
      }
      try {
        const resp = await fetch(url);
        if (resp.ok) {
          resolve();
          return;
        }
      } catch {
        // 未就绪, 继续轮询
      }
      setTimeout(tick, 500);
    };
    tick();
  });
}

async function main() {
  // 0) 校验必要依赖
  const tscEntry = bin("typescript", "bin", "tsc");
  const viteEntry = bin("vite", "bin", "vite.js");
  const electronExe = isWin
    ? bin("electron", "dist", "electron.exe")
    : bin("electron", "dist", "electron");
  for (const [name, p] of [
    ["typescript", tscEntry],
    ["vite", viteEntry],
    ["electron", electronExe],
  ]) {
    if (!existsSync(p)) {
      console.error(`[start] 缺少依赖: ${name} (${p}), 请先执行 npm install`);
      process.exit(1);
    }
  }

  // 1) 编译主进程 TS → dist-main
  console.log("[start] 编译主进程 (tsc) ...");
  await new Promise((resolve, reject) => {
    const tsc = runNode(tscEntry, ["-p", "tsconfig.main.json"], { cwd: root });
    tsc.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`主进程编译失败 (tsc exit=${code})`));
    });
  });

  // 2) 启动 vite dev server(strictPort: 5173 被占用时报错而非静默换端口)
  console.log("[start] 启动 vite dev server ...");
  const viteEnv = { ...process.env };
  delete viteEnv.NODE_OPTIONS; // 清除沙箱 shim, 避免干扰 vite 文件操作
  const vite = runNode(viteEntry, ["--port", "5173", "--strictPort"], {
    cwd: root,
    env: viteEnv,
  });

  try {
    await waitForPort("http://localhost:5173");
    console.log("[start] vite 就绪 → 启动 Electron");
    // 构造 electron 环境: 显式删除可能使其以纯 Node 模式运行的变量
    // (Windows 上 spawn env 值为 undefined 的属性会被序列化而非跳过, 必须 delete)
    const electronEnv = { ...process.env, VITE_DEV_SERVER_URL: "http://localhost:5173" };
    delete electronEnv.ELECTRON_RUN_AS_NODE;
    delete electronEnv.NODE_OPTIONS;
    const electron = spawn(electronExe, ["."], { cwd: root, env: electronEnv });
    electron.on("exit", (code) => {
      console.log(`[start] Electron 退出 (code=${code}), 清理 vite ...`);
      vite.kill();
      process.exit(code ?? 0);
    });
    electron.on("error", (err) => {
      console.error("[start] Electron 启动失败:", err);
      vite.kill();
      process.exit(1);
    });
  } catch (err) {
    console.error("[start] 启动失败:", err);
    vite.kill();
    process.exit(1);
  }
}

main();
