// 检查更新 + 应用内一键升级(2026-08-06 完善):
// - checkForUpdates(): GitHub Releases API 版本对比(已有)
// - downloadUpdate(): 下载最新 NSIS 安装器(Setup exe)到临时目录,
//   流式进度回调 + sha256 校验(GitHub asset digest)
// - installUpdate(): 静默安装(/S, electron-builder NSIS 默认装完自动启动
//   新版本)并退出本进程
// 更新源: package.json build.publish 或环境变量 PA_UPDATE_REPO(owner/repo);
// 默认 zongxin/private-agent(施工文件夹发布脚本 scripts/publish-release.mjs
// 打 tag 并上传构建产物)。
import { app } from "electron";
import { createHash } from "crypto";
import { createWriteStream } from "fs";
import { join } from "path";
import { spawn } from "child_process";

// 版本号解析: "0.1.0" / "v0.1.0" / "0.1.0-beta.1" → [0,1,0,...]
function parseVersion(v: string): number[] {
  return String(v)
    .replace(/^v/i, "")
    .split(/[.\-+]/)
    .map((s) => parseInt(s, 10) || 0);
}

/** a > b 返回 1, a < b 返回 -1, 相等返回 0 */
function compareVersions(a: string, b: string): number {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (x > y) return 1;
    if (x < y) return -1;
  }
  return 0;
}

export interface UpdateCheckResult {
  hasUpdate: boolean;
  currentVersion: string;
  latestVersion: string;
  releaseUrl: string;
  notes?: string;
  failed?: boolean;
  /** 最新 release 的安装器资产(下载/安装用) */
  asset?: { name: string; url: string; size: number; sha256?: string };
}

function repoName(): string {
  return process.env.PA_UPDATE_REPO || "jkfzhsz/private-agent";
}

interface ReleaseAsset {
  name: string;
  browser_download_url: string;
  size: number;
  digest?: string;
}

async function fetchLatestRelease(): Promise<{
  tag_name: string;
  html_url: string;
  body: string;
  assets: ReleaseAsset[];
}> {
  const apiUrl = `https://api.github.com/repos/${repoName()}/releases/latest`;
  const resp = await fetch(apiUrl, {
    headers: { Accept: "application/vnd.github+json", "User-Agent": "private-agent" },
    signal: AbortSignal.timeout(15000),
  });
  if (!resp.ok) throw new Error(`GitHub API HTTP ${resp.status}`);
  return (await resp.json()) as ReturnType<typeof fetchLatestRelease> extends Promise<
    infer T
  >
    ? T
    : never;
}

export async function checkForUpdates(): Promise<UpdateCheckResult> {
  const currentVersion = app.getVersion() || "0.1.0";
  try {
    const rel = await fetchLatestRelease();
    const latestVersion = rel.tag_name ?? "";
    const hasUpdate = latestVersion !== "" && compareVersions(latestVersion, currentVersion) > 0;
    // 找 NSIS 安装器资产(Setup*.exe)
    const installer = rel.assets.find((a) => /Setup.*\.exe$/i.test(a.name));
    const asset = installer
      ? {
          name: installer.name,
          url: installer.browser_download_url,
          size: installer.size,
          sha256: installer.digest
            ? installer.digest.replace(/^sha256:/i, "").toLowerCase()
            : undefined,
        }
      : undefined;
    return {
      hasUpdate,
      currentVersion,
      latestVersion: latestVersion || "未知",
      releaseUrl: rel.html_url ?? "",
      notes: rel.body?.slice(0, 500) ?? "",
      asset: hasUpdate ? asset : undefined,
    };
  } catch (e) {
    return {
      hasUpdate: false,
      currentVersion,
      latestVersion: "",
      releaseUrl: "",
      notes: String(e),
      failed: true,
    };
  }
}

export interface DownloadResult {
  path: string;
  size: number;
  sha256: string;
  error?: string;
}

/** 下载安装器到临时目录, 流式进度回调(percent 0-100), sha256 校验。 */
export async function downloadUpdate(
  asset: { url: string; name: string; sha256?: string },
  onProgress?: (received: number, total: number, percent: number) => void
): Promise<DownloadResult> {
  const target = join(app.getPath("temp"), `private-agent-setup-${Date.now()}.exe`);
  const resp = await fetch(asset.url, {
    headers: { Accept: "application/octet-stream", "User-Agent": "private-agent" },
    signal: AbortSignal.timeout(600000),
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`下载失败: HTTP ${resp.status}`);
  }
  const total = Number(resp.headers.get("content-length")) || 0;
  const hash = createHash("sha256");
  let received = 0;
  const reader = resp.body.getReader();
  const file = createWriteStream(target);
  // 手动流式(可算进度 + 哈希)
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    file.write(value);
    hash.update(value);
    received += value.length;
    if (total > 0 && onProgress) {
      onProgress(received, total, Math.min(100, Math.round((received / total) * 100)));
    }
  }
  await new Promise<void>((resolve, reject) => {
    file.end(() => resolve());
    file.on("error", reject);
  });
  const sha256 = hash.digest("hex");
  if (asset.sha256 && sha256 !== asset.sha256.toLowerCase()) {
    throw new Error(`校验失败: 下载文件 sha256 与发布资产不一致(可能被篡改)`);
  }
  if (onProgress && total > 0) onProgress(received, total, 100);
  return { path: target, size: received, sha256 };
}

/** 静默安装(/S)并退出本进程 —— electron-builder NSIS 默认装完自动启动新版本。 */
export function installUpdate(installerPath: string): void {
  const child = spawn(installerPath, ["/S"], {
    detached: true,
    stdio: "ignore",
    windowsHide: true,
  });
  child.unref();
  // 稍等安装器拉起, 本进程退出
  setTimeout(() => {
    app.exit(0);
  }, 800);
}
