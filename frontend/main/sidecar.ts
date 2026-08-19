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
  /** 后端进程工作目录(应为 backend 目录, 使 config.yaml 中 ./skills 等相对路径解析正确) */
  cwd?: string;
}

export const MAX_RESTARTS = 3;
export const RESTART_DELAYS_MS = [1000, 2000, 4000];

export function spawnSidecar(config: SidecarConfig): ChildProcess {
  return spawn(config.pythonCommand, ["-m", config.moduleName], {
    env: { ...process.env, ...config.env },
    stdio: ["pipe", "pipe", "pipe"],
    cwd: config.cwd,
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
    const proc = spawnSidecar(this.config);
    this.proc = proc;

    // 收集子进程 stderr, 启动失败时用于诊断(如端口被占 → bind 失败)
    let capturedStderr = "";
    proc.stderr?.on("data", (d: Buffer) => {
      capturedStderr += String(d);
    });

    // 等待 health OK; 若子进程提前退出(端口被占/启动失败)则立即报错, 不等超时
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const onExit = (code: number | null) => {
        if (settled) return;
        settled = true;
        const tail = capturedStderr.trim().slice(-400);
        reject(
          new Error(
            `Sidecar 进程提前退出 (code=${code})${tail ? `: ${tail}` : ""}` +
              `\n(若端口 ${this.config.port} 被占用, 请先关闭占用该端口的进程)`
          )
        );
      };
      proc.once("exit", onExit);
      waitForHealth(this.config.healthUrl)
        .then(() => {
          if (settled) return;
          settled = true;
          resolve();
        })
        .catch((e: unknown) => {
          if (settled) return;
          settled = true;
          reject(e);
        });
    });

    // health OK 后的常驻崩溃监控: 自动重启(≤3 次, 指数退避)
    proc.on("exit", (code) => {
      if (this.stopped) return;
      if (this.restarts < MAX_RESTARTS) {
        const delay = RESTART_DELAYS_MS[this.restarts] ?? RESTART_DELAYS_MS[0];
        this.restarts += 1;
        setTimeout(() => {
          void this.start();
        }, delay);
      }
    });
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.proc) {
      await stopSidecar(this.proc);
      this.proc = null;
    }
  }
}
