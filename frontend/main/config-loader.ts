// B2 P1-1 - 读取 config.yaml 中的 Sidecar 配置
import { readFileSync } from "fs";
import { join } from "path";
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
    pythonCommand: sidecar.python_command ?? "python",
    moduleName: "private_agent.main",
    port: doc?.server?.http?.port ?? 8765,
  };
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
