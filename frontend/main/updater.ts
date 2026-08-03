// 检查更新(轻量版): 通过 GitHub Releases API 对比版本号。
// 更新源配置: package.json 的 build.publish 或环境变量 PA_UPDATE_REPO
// (格式 "owner/repo"); 未配置时用默认值。检查失败不阻塞应用。
import { app } from "electron";

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
}

export async function checkForUpdates(): Promise<UpdateCheckResult> {
  const currentVersion = app.getVersion() || "0.1.0";
  const repo =
    process.env.PA_UPDATE_REPO || "zongxin/private-agent";
  const apiUrl = `https://api.github.com/repos/${repo}/releases/latest`;
  try {
    const resp = await fetch(apiUrl, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "private-agent" },
      signal: AbortSignal.timeout(10000),
    });
    if (!resp.ok) {
      throw new Error(`GitHub API HTTP ${resp.status}`);
    }
    const data = (await resp.json()) as {
      tag_name?: string;
      html_url?: string;
      body?: string;
    };
    const latestVersion = data.tag_name ?? "";
    const releaseUrl = data.html_url ?? apiUrl;
    const hasUpdate = latestVersion !== "" && compareVersions(latestVersion, currentVersion) > 0;
    return {
      hasUpdate,
      currentVersion,
      latestVersion: latestVersion || "未知",
      releaseUrl,
      notes: data.body?.slice(0, 500) ?? "",
    };
  } catch (e) {
    return {
      hasUpdate: false,
      currentVersion,
      latestVersion: "",
      releaseUrl: "",
      notes: String(e),
      // 检查失败: 通过 latestVersion 空字符串表达"检查失败"
      ...({ failed: true } as object),
    } as UpdateCheckResult;
  }
}
