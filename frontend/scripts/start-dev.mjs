// Phase 1 Task 13 - 一键启动脚本(dev 模式)
// 流程: 编译主进程(tsc) → 启动 vite dev server → 等待 5173 就绪 →
//       启动 electron(注入 VITE_DEV_SERVER_URL) → electron 退出时清理 vite
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const isWin = process.platform === "win32";
const npxCmd = isWin ? "npx.cmd" : "npx";

function run(cmd, args, opts = {}) {
  const child = spawn(cmd, args, { stdio: "inherit", ...opts });
  return child;
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
  // 1) 编译主进程 TS → dist-main
  console.log("[start] 编译主进程 (tsc -p tsconfig.main.json) ...");
  await new Promise((resolve, reject) => {
    const tsc = run(npxCmd, ["tsc", "-p", "tsconfig.main.json"], { cwd: root });
    tsc.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`主进程编译失败 (tsc exit=${code})`));
    });
  });

  // 2) 启动 vite dev server
  console.log("[start] 启动 vite dev server ...");
  const vite = run(npxCmd, ["vite"], { cwd: root });

  try {
    await waitForPort("http://localhost:5173");
    console.log("[start] vite 就绪 → 启动 Electron");
    const electron = run(npxCmd, ["electron", "."], {
      cwd: root,
      env: { ...process.env, VITE_DEV_SERVER_URL: "http://localhost:5173" },
    });
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
