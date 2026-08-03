// B2 P1-1 - 读取 config.yaml 中的 Sidecar 配置
// Phase 1 Task 13: python_command 支持智能探测(优先 backend/.venv 解释器)
import { existsSync, readFileSync } from "fs";
import { dirname, join } from "path";
import { load } from "js-yaml";

export interface LoadedSidecarConfig {
  pythonCommand: string;
  moduleName: string;
  port: number;
  /** backend 目录(用于注入 WORKSPACE 环境变量, 后端 workspace_root=${WORKSPACE}) */
  workspaceRoot: string;
}

interface RawConfig {
  server?: { http?: { port?: number } };
  system?: { sidecar?: { python_command?: string } };
}

export function loadSidecarConfig(configPath?: string): LoadedSidecarConfig {
  const resolved = configPath ?? findConfigPath();
  const doc = load(readFileSync(resolved, "utf-8")) as RawConfig;

  const sidecar = doc?.system?.sidecar ?? {};
  return {
    pythonCommand: sidecar.python_command ?? detectPythonCommand(resolved),
    moduleName: "private_agent.main",
    port: doc?.server?.http?.port ?? 8765,
    workspaceRoot: dirname(dirname(resolved)), // backend/config/config.yaml → backend/
  };
}

/** 探测可用的 Python 解释器: config.yaml 显式配置 > 打包资源 venv > backend/.venv > python */
function detectPythonCommand(configPath: string): string {
  // 打包后: resourcesPath/backend/.venv/Scripts/python.exe
  const packaged = packagedBackendDir();
  if (packaged) {
    const packagedPy = join(packaged, ".venv", "Scripts", "python.exe");
    if (existsSync(packagedPy)) return packagedPy;
  }
  // config.yaml 位于 backend/config/, 上一级即 backend 目录
  const backendDir = dirname(dirname(configPath));
  const venvPy =
    process.platform === "win32"
      ? join(backendDir, ".venv", "Scripts", "python.exe")
      : join(backendDir, ".venv", "bin", "python");
  if (existsSync(venvPy)) return venvPy;
  return "python";
}

/** 打包后的资源目录(extraResources/backend): process.resourcesPath/backend */
function packagedBackendDir(): string | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { app } = require("electron") as typeof import("electron");
    if (!app?.isPackaged) return null;
    const p = join(app.getAppPath() ? process.resourcesPath : "", "backend");
    return existsSync(p) ? p : null;
  } catch {
    return null;
  }
}

function findConfigPath(): string {
  // 打包后: resourcesPath/backend/config/config.yaml
  const packaged = packagedBackendDir();
  const candidates = [
    // 优先相对进程工作目录查找 backend/config/config.yaml(frontend/ 下运行时)
    join(process.cwd(), "backend", "config", "config.yaml"),
    join(process.cwd(), "..", "backend", "config", "config.yaml"),
    join(process.cwd(), "config.yaml"),
    // 部署目录(D:\PA1.0 自包含)优先于项目根
    "D:\\PA1.0\\backend\\config\\config.yaml",
    // 轻度打包: 后端复用项目根目录(D:\Private agent\backend)
    "D:\\Private agent\\backend\\config\\config.yaml",
  ];
  if (packaged) {
    candidates.unshift(join(packaged, "config", "config.yaml"));
  }
  for (const candidate of candidates) {
    try {
      readFileSync(candidate, "utf-8");
      return candidate;
    } catch {
      // 继续尝试下一个候选路径
    }
  }
  throw new Error("config.yaml not found");
}

