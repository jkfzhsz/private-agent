// B2 P1-1 - Electron 主进程 Sidecar 管理
//
// 功能:
// - spawnSidecar: 启动 python -m private_agent.main 子进程
// - waitForHealth: 轮询 /health 直到 200
// - stopSidecar: SIGTERM → 超时 SIGKILL
// - SidecarManager: 崩溃自动重启(≤3 次,指数退避 1s/2s/4s)
//
// 2026-09-29(崩溃取证) —— 自我改动断连诊断的直接产物:
//
// 缺陷: 子进程的 stdout/stderr **完全没有落盘**。只有 stderr 被累积进一个内存
// 变量, 且**仅在"启动失败"路径**才被使用; 运行期崩溃(exit)时既不记录退出码,
// 也不留任何输出, 崩溃后的自动重启同样是静默的。
// 后果(实测): 2026-09-29 16:27 一轮自我改动导致后端异常退出 —— agent.log 中
// `Sidecar started` 之前**没有任何 `Sidecar shutdown` 记录**(按 main.py 自带
// 判据 = 非优雅退出/被强杀), 但崩溃现场零痕迹, 用户与 PA 都无法判断原因
// (蒋先生原话: "我无法判断到底是什么原因造成失败")。
//
// 另修一处真实隐患: 原 stdio=["pipe","pipe","pipe"] 却**从不读取 stdout** ——
// 管道缓冲区写满后子进程会阻塞在 write 上(日志量大时尤甚)。
//
// 本版改动:
// 1. stdout/stderr 全量落盘 → <workspace>/logs/sidecar.log(与 agent.log 同目录)
// 2. exit 记录 code/signal/时间/是否优雅 + 输出尾部(内存各留 4KB, 崩溃时补写)
// 3. 崩溃重启与"重启次数用尽"均显式记录(不再静默)
// 4. 消除 stdout 无人消费的阻塞隐患
import { spawn } from "child_process";
import type { ChildProcess } from "child_process";
import { appendFileSync, createWriteStream, mkdirSync } from "fs";
import type { WriteStream } from "fs";
import { dirname, join } from "path";
import type { Readable } from "stream";

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

/** 崩溃取证: 内存中保留的输出尾部长度(退出时补写入日志, 保证现场完整) */
const TAIL_LIMIT = 4096;
/** 启动失败诊断用的 stderr 上限 */
const STARTUP_STDERR_LIMIT = 8000;

export function spawnSidecar(config: SidecarConfig): ChildProcess {
  return spawn(config.pythonCommand, ["-m", config.moduleName], {
    env: { ...process.env, ...config.env },
    stdio: ["pipe", "pipe", "pipe"],
    cwd: config.cwd,
  });
}

/**
 * sidecar 事件日志路径。
 *
 * 与后端 workspace_root 保持一致: dev → `<backend>/logs`, 打包 → `<userData>/logs`
 * (index.ts 注入 WORKSPACE / PA_USER_DATA, 见 main/index.ts:157-160)。
 */
export function resolveSidecarLogPath(config: SidecarConfig): string {
  const root =
    config.env?.PA_USER_DATA || config.env?.WORKSPACE || config.cwd || ".";
  return join(root, "logs", "sidecar.log");
}

/** 追加一行事件日志(同步写 —— 崩溃前必须已落盘, 不能用异步流) */
function logEvent(logPath: string, line: string): void {
  try {
    mkdirSync(dirname(logPath), { recursive: true });
    appendFileSync(logPath, `[${new Date().toISOString()}] ${line}\n`, "utf-8");
  } catch {
    /* 日志写入失败不应影响主流程 */
  }
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

  private get logPath(): string {
    return resolveSidecarLogPath(this.config);
  }

  async start(): Promise<void> {
    this.stopped = false;
    const logPath = this.logPath;
    const proc = spawnSidecar(this.config);
    this.proc = proc;

    // stdout/stderr 全量落盘(流式写) + 内存尾部(崩溃时同步补写, 保证现场完整)
    let logStream: WriteStream | null = null;
    try {
      mkdirSync(dirname(logPath), { recursive: true });
      logStream = createWriteStream(logPath, { flags: "a" });
    } catch {
      logStream = null;
    }

    let stdoutTail = "";
    let stderrTail = "";
    let capturedStderr = ""; // 启动失败时用于诊断(限长)

    const attach = (stream: Readable | null, isErr: boolean): void => {
      if (!stream) return;
      stream.on("data", (chunk: Buffer | string) => {
        const text = String(chunk);
        if (logStream) logStream.write(text);
        if (isErr) {
          stderrTail = (stderrTail + text).slice(-TAIL_LIMIT);
          capturedStderr = (capturedStderr + text).slice(-STARTUP_STDERR_LIMIT);
        } else {
          stdoutTail = (stdoutTail + text).slice(-TAIL_LIMIT);
        }
      });
      // 显式消费流, 避免管道缓冲区写满导致子进程阻塞
      stream.on("error", () => {
        /* 忽略流错误(进程退出时常见) */
      });
    };
    attach(proc.stdout, false);
    attach(proc.stderr, true);

    logEvent(
      logPath,
      `spawn pid=${proc.pid ?? "?"} cmd=${this.config.pythonCommand} ` +
        `-m ${this.config.moduleName} cwd=${this.config.cwd ?? "?"} ` +
        `restarts=${this.restarts}/${MAX_RESTARTS}`,
    );

    // 等待 health OK; 若子进程提前退出(端口被占/启动失败)则立即报错, 不等超时
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const onExit = (code: number | null, signal: NodeJS.Signals | null) => {
        if (settled) return;
        settled = true;
        const tail = capturedStderr.trim().slice(-400);
        logEvent(
          logPath,
          `startup FAILED: exited before health OK code=${code} signal=${signal}` +
            (tail ? ` stderr=${tail}` : ""),
        );
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

    logEvent(logPath, `health OK pid=${proc.pid ?? "?"}`);

    // health OK 后的常驻崩溃监控: 自动重启(≤3 次, 指数退避)
    // 2026-09-29: 无论优雅/崩溃都落盘现场; 崩溃时补写输出尾部; 重启与放弃均记录。
    proc.on("exit", (code, signal) => {
      const graceful = this.stopped;
      logEvent(
        logPath,
        `${graceful ? "stopped (graceful)" : "EXITED (unexpected)"} ` +
          `code=${code} signal=${signal} pid=${proc.pid ?? "?"}`,
      );
      if (!graceful) {
        // 崩溃现场: 输出尾部同步补写(流式部分可能尚未 flush)
        logEvent(
          logPath,
          `--- stdout tail ---\n${stdoutTail.trim() || "(empty)"}\n--- stderr tail ---\n${stderrTail.trim() || "(empty)"}`,
        );
      }
      try {
        logStream?.end();
      } catch {
        /* ignore */
      }

      if (graceful) return;
      if (this.restarts < MAX_RESTARTS) {
        const delay = RESTART_DELAYS_MS[this.restarts] ?? RESTART_DELAYS_MS[0];
        const attempt = this.restarts + 1;
        this.restarts += 1;
        logEvent(logPath, `restart #${attempt}/${MAX_RESTARTS} scheduled in ${delay}ms`);
        setTimeout(() => {
          void this.start();
        }, delay);
      } else {
        logEvent(
          logPath,
          `ERROR: sidecar 已崩溃且重启次数用尽(${MAX_RESTARTS} 次) —— ` +
            `后端将不可用。请查看本文件上方崩溃现场(exit code / stderr tail), ` +
            `常见原因: 内存不足(本机 7.6GB) / 端口占用 / 代码异常。`,
        );
      }
    });
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.proc) {
      logEvent(
        this.logPath,
        `stopping pid=${this.proc.pid ?? "?"} (SIGTERM → 30s → SIGKILL)`,
      );
      await stopSidecar(this.proc);
      this.proc = null;
    }
  }
}
