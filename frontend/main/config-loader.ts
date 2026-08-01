// B2 P1-1 - 读取 config.yaml 中的 Sidecar 配置
// Phase 1 Task 13: python_command 支持智能探测(优先 backend/.venv 解释器)
import { existsSync, readFileSync } from "fs";
import { dirname, join } from "path";
import { load } from "js-yaml";

export interface LoadedSidecarConfig {
  pythonCommand: string;
  moduleName: string;
  port: number;
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
  };
}

/** 探测可用的 Python 解释器: config.yaml 显式配置 > backend/.venv > python */
function detectPythonCommand(configPath: string): string {
  // config.yaml 位于 backend/config/, 上一级即 backend 目录
  const backendDir = dirname(dirname(configPath));
  const venvPy =
    process.platform === "win32"
      ? join(backendDir, ".venv", "Scripts", "python.exe")
      : join(backendDir, ".venv", "bin", "python");
  if (existsSync(venvPy)) return venvPy;
  return "python";
}

function findConfigPath(): string {
  // 优先相对进程工作目录查找 backend/config/config.yaml(frontend/ 下运行时)
  const candidates = [
    join(process.cwd(), "backend", "config", "config.yaml"),
    join(process.cwd(), "..", "backend", "config", "config.yaml"),
    join(process.cwd(), "config.yaml"),
  ];
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
