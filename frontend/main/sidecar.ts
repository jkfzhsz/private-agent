// B2 P1-1 - Electron 主进程 Sidecar 管理
//
// 功能:
// - spawnSidecar: 启动 python -m private_agent.main 子进程
// - waitForHealth: 轮询 /health 直到 200
// - stopSidecar: SIGTERM → 超时 SIGKILL
// - SidecarManager: 崩溃自动重启(≤3 次,指数退避 1s/2s/4s)
import { spawn } from "child_process";
import type { ChildProcess } from "child_process";

export interface SidecarConfig {
  pythonCommand: string;
  moduleName: string;
  port: number;
  healthUrl: string;
  env?: NodeJS.ProcessEnv;
}

export const MAX_RESTARTS = 3;
export const RESTART_DELAYS_MS = [1000, 2000, 4000];

export function spawnSidecar(config: SidecarConfig): ChildProcess {
  return spawn(config.pythonCommand, ["-m", config.moduleName], {
    env: { ...process.env, ...config.env },
    stdio: ["pipe", "pipe", "pipe"],
  });
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function waitForHealth(healthUrl: string, timeoutMs = 30000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(healthUrl);
      if (resp.ok) return;
    } catch {
      // 连接失败,继续重试
    }
    await sleep(500);
  }
  throw new Error(`Sidecar health check timed out after ${timeoutMs}ms`);
}

export async function stopSidecar(proc: ChildProcess, timeoutMs = 30000): Promise<void> {
  if (proc.exitCode !== null) return; // 已退出
  proc.kill("SIGTERM");
  await Promise.race([
    new Promise<void>((resolve) => proc.once("exit", () => resolve())),
    sleep(timeoutMs).then(() => {
      if (proc.exitCode === null) {
        proc.kill("SIGKILL");
      }
    }),
  ]);
}

export class SidecarManager {
  private proc: ChildProcess | null = null;
  private restarts = 0;
  private stopped = false;

  constructor(private readonly config: SidecarConfig) {}

  get process(): ChildProcess | null {
    return this.proc;
  }

  async start(): Promise<void> {
    this.stopped = false;
    this.proc = spawnSidecar(this.config);
    this.proc.on("exit", (code) => {
      if (this.stopped) return;
      if (this.restarts < MAX_RESTARTS) {
        const delay = RESTART_DELAYS_MS[this.restarts] ?? RESTART_DELAYS_MS[0];
        this.restarts += 1;
        setTimeout(() => {
          void this.start();
        }, delay);
      }
    });
    await waitForHealth(this.config.healthUrl);
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.proc) {
      await stopSidecar(this.proc);
      this.proc = null;
    }
  }
}
