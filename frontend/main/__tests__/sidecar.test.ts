// B2 P1-1 - Electron 主进程拉起 Python Sidecar 测试
//
// Source: plan/b2-remaining-features step 15-20 (修复计划 §2 P1-1)
// - spawnSidecar 用正确参数启动 python -m private_agent.main
// - waitForHealth 轮询 /health 直到 200,超时抛错
// - stopSidecar 先 SIGTERM,超时 SIGKILL
// - SidecarManager 崩溃重启 ≤3 次 + 指数退避
import { ChildProcess } from "child_process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SidecarManager,
  spawnSidecar,
  stopSidecar,
  waitForHealth,
  type SidecarConfig,
} from "../sidecar";

vi.mock("child_process", () => {
  const mockSpawn = vi.fn();
  return {
    __esModule: true,
    default: { spawn: mockSpawn },
    spawn: mockSpawn,
  };
});

import { spawn } from "child_process";

const mockSpawn = vi.mocked(spawn);

function makeConfig(overrides: Partial<SidecarConfig> = {}): SidecarConfig {
  return {
    pythonCommand: "python",
    moduleName: "private_agent.main",
    port: 8765,
    healthUrl: "http://127.0.0.1:8765/health",
    ...overrides,
  };
}

function makeFakeProc(): ChildProcess & { _exitCb: ((code?: number) => void) | null } {
  const fake = {
    kill: vi.fn(),
    exitCode: null,
    on: vi.fn((event: string, cb: (code?: number) => void) => {
      if (event === "exit") fake._exitCb = cb;
      return fake;
    }),
    once: vi.fn((event: string, cb: (code?: number) => void) => {
      if (event === "exit") fake._exitCb = cb;
      return fake;
    }),
    _exitCb: null as ((code?: number) => void) | null,
  } as unknown as ChildProcess & { _exitCb: ((code?: number) => void) | null };
  return fake;
}

beforeEach(() => {
  mockSpawn.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("spawnSidecar", () => {
  it("用 python -m private_agent.main 正确启动", () => {
    const fakeProc = makeFakeProc();
    mockSpawn.mockReturnValue(fakeProc);

    const proc = spawnSidecar(makeConfig());

    expect(mockSpawn).toHaveBeenCalledWith(
      "python",
      ["-m", "private_agent.main"],
      expect.objectContaining({ stdio: ["pipe", "pipe", "pipe"] }),
    );
    expect(proc).toBe(fakeProc);
  });
});

describe("waitForHealth", () => {
  it("轮询 /health 直到返回 200", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    const promise = waitForHealth("http://127.0.0.1:8765/health", 5000);
    await vi.advanceTimersByTimeAsync(1500);
    await expect(promise).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("超时抛错", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));

    const promise = waitForHealth("http://127.0.0.1:8765/health", 2000);
    const expectation = expect(promise).rejects.toThrow(/timed out/i);
    await vi.advanceTimersByTimeAsync(3000);
    await expectation;
  });
});

describe("stopSidecar", () => {
  it("先 SIGTERM,进程退出后不再 SIGKILL", async () => {
    const fakeProc = makeFakeProc();
    fakeProc.on("exit", (cb) => {
      fakeProc._exitCb = cb as unknown as (code?: number) => void;
      return fakeProc;
    });

    const promise = stopSidecar(fakeProc as unknown as ChildProcess, 5000);
    expect(fakeProc.kill).toHaveBeenCalledWith("SIGTERM");

    // 模拟进程在超时前退出
    fakeProc._exitCb!(0);
    await expect(promise).resolves.toBeUndefined();
    expect(fakeProc.kill).toHaveBeenCalledTimes(1);
  });
});

describe("SidecarManager", () => {
  it("崩溃后自动重启,指数退避,最多 3 次", async () => {
    const fakeProcs = [makeFakeProc(), makeFakeProc(), makeFakeProc(), makeFakeProc()];
    let spawnCount = 0;
    mockSpawn.mockImplementation(() => fakeProcs[spawnCount++]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

    const mgr = new SidecarManager(makeConfig());
    const startPromise = mgr.start();
    await vi.runAllTimersAsync();
    await startPromise;
    expect(spawnCount).toBe(1);

    // 触发第一次崩溃
    fakeProcs[0]._exitCb!(1);
    // 退避 1s 后第二次启动
    await vi.advanceTimersByTimeAsync(1000);
    await vi.runAllTimersAsync();
    expect(spawnCount).toBe(2);

    // 第二次崩溃 → 退避 2s
    fakeProcs[1]._exitCb!(1);
    await vi.advanceTimersByTimeAsync(2000);
    await vi.runAllTimersAsync();
    expect(spawnCount).toBe(3);

    // 第三次崩溃 → 退避 4s
    fakeProcs[2]._exitCb!(1);
    await vi.advanceTimersByTimeAsync(4000);
    await vi.runAllTimersAsync();
    expect(spawnCount).toBe(4);

    // 第四次崩溃 → 超过 3 次,不再重启
    fakeProcs[3]._exitCb!(1);
    await vi.advanceTimersByTimeAsync(10000);
    await vi.runAllTimersAsync();
    expect(spawnCount).toBe(4);
  });
});
