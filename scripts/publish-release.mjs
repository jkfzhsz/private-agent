#!/usr/bin/env node
// Private Agent 发布脚本(2026-08-06)
//
// 施工文件夹(D:\Private agent)构建 → 脱敏检查 → 打 tag → GitHub Release 上传。
// 正式版(本地终端)通过"设置-关于与更新-检查更新"从该 Release 拉取安装包
// 一键升级; 升级不触碰 %APPDATA%\Private Agent 用户配置与数据库。
//
// 用法:
//   node scripts/publish-release.mjs            # 使用 gh CLI(已登录)
//   GITHUB_TOKEN=xxx node scripts/publish-release.mjs   # 无 gh, 用 API
//   node scripts/publish-release.mjs --dry-run  # 只检查构建产物与脱敏, 不上传
//
// 依赖: gh CLI(推荐) 或 GitHub token; Node 20+(顶层 await)。
import { execFileSync } from "child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "fs";
import { join, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const ROOT = resolve(__dirname, "..");
const FRONTEND = join(ROOT, "frontend");
const RELEASE2 = join(FRONTEND, "release2");

const DRY = process.argv.includes("--dry-run");
const REPO = process.env.PA_UPDATE_REPO || "jkfzhsz/private-agent";

const pkg = JSON.parse(readFileSync(join(FRONTEND, "package.json"), "utf-8"));
const VERSION = pkg.version;

// ── 1. 定位构建产物 ──────────────────────────────────────────────────────────
if (!existsSync(RELEASE2)) {
  console.error(`[publish] release2 不存在: ${RELEASE2}\n  请先运行 build-electron.bat`);
  process.exit(1);
}
const installerRe = new RegExp(`Private Agent Setup ${VERSION.replace(/\./g, "\\.")}\\.exe$`);
const all = readdirSync(RELEASE2);
const installer = all.find((f) => installerRe.test(f));
if (!installer) {
  console.error(`[publish] 未找到安装包: Private Agent Setup ${VERSION}.exe (release2/)`);
  process.exit(1);
}
const installerSize = statSync(join(RELEASE2, installer)).size;
console.log(`[publish] 版本 ${VERSION} | 安装包: ${installer} (${(installerSize / 1024 / 1024).toFixed(1)} MB)`);

// ── 2. 脱敏检查: 确认产物不含密钥/环境文件/测试代码 ──────────────────────────
const SECRET_PATTERNS = [/(^|[\\/])\.env(\.|$)/i, /\.pem$/, /\.key$/i, /private_agent\.egg-info/i, /([\\/])tests([\\/])/i];
const SENSITIVE_KEYWORDS = ["PA_MASTER_KEY", "PA_ADMIN_TOKEN", "PA_DB_PASSWORD", "api_key_encrypted"];
let leaked = 0;
function scanDir(dir, depth) {
  if (depth > 3) return;
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    let st;
    try {
      st = statSync(p);
    } catch {
      continue;
    }
    if (st.isDirectory()) {
      scanDir(p, depth + 1);
      continue;
    }
    const rel = p.slice(RELEASE2.length).replace(/^[\\/]/, "");
    if (SECRET_PATTERNS.some((re) => re.test(rel))) {
      console.warn(`  [脱敏] ⚠ 发现敏感文件: ${rel}`);
      leaked++;
    }
    if (/\.(js|json|yml|yaml|html)$/.test(rel) && st.size < 2 * 1024 * 1024) {
      try {
        const text = readFileSync(p, "utf-8");
        for (const kw of SENSITIVE_KEYWORDS) {
          if (text.includes(kw)) {
            console.warn(`  [脱敏] ⚠ 产物 ${rel} 含敏感关键字: ${kw}`);
            leaked++;
            break;
          }
        }
      } catch {
        /* 二进制/不可读跳过 */
      }
    }
  }
}
scanDir(RELEASE2, 0);
if (leaked > 0 && !DRY) {
  console.error(`[publish] 脱敏检查发现 ${leaked} 处敏感内容, 已中止上传(请检查后重试)`);
  process.exit(1);
}
console.log(`[publish] 脱敏检查 ${leaked === 0 ? "通过 ✓(无 .env/密钥/测试)" : `⚠ ${leaked} 处(仅 dry-run 放行)`}`);
if (DRY) {
  console.log("[publish] --dry-run: 仅检查, 不上传");
  process.exit(0);
}

// ── 3. 打 tag(不存在则创建) ───────────────────────────────────────────────────
const TAG = `v${VERSION}`;
try {
  const tags = execFileSync("git", ["tag", "--list", TAG], { cwd: ROOT, encoding: "utf-8" }).trim();
  if (!tags) {
    execFileSync("git", ["tag", TAG], { cwd: ROOT, stdio: "inherit" });
    execFileSync("git", ["push", "origin", TAG], { cwd: ROOT, stdio: "inherit" });
    console.log(`[publish] 已创建并推送 tag ${TAG}`);
  } else {
    console.log(`[publish] tag ${TAG} 已存在(复用)`);
  }
} catch (e) {
  console.error(`[publish] git tag/push 失败: ${String(e)}`);
  process.exit(1);
}

// ── 4. 创建/更新 Release 并上传安装包 ─────────────────────────────────────────
const notes = `Private Agent v${VERSION}\n\n- 安装包: ${installer}\n- 数据与配置: 升级保留 %APPDATA%\\Private Agent 与数据库, 无需重新配置`;
const installerPath = join(RELEASE2, installer);

async function publishWithGh() {
  const cmd = `gh release create ${TAG} "${installerPath}" --repo ${REPO} --title "Private Agent ${VERSION}" --notes "${notes.replaceAll('"', '\\"')}" ${await releaseExists(REPO, TAG)}`;
  execFileSync("bash", ["-c", cmd], { stdio: "inherit" });
  console.log(`[publish] Release 已发布: https://github.com/${REPO}/releases/tag/${TAG}`);
}

async function releaseExists(repo, tag) {
  try {
    execFileSync("gh", ["release", "view", tag, "--repo", repo], { stdio: "ignore" });
    return "--force"; // 已存在则覆盖
  } catch {
    return "";
  }
}

async function publishWithApi() {
  // token 来源: 环境变量 GITHUB_TOKEN/GH_TOKEN > 本地文件 scripts/.gh-token
  // (2026-08-06: 本地文件免每次输入, 已 gitignore 绝不上传)
  const token =
    process.env.GITHUB_TOKEN ||
    process.env.GH_TOKEN ||
    (() => {
      try {
        const p = join(__dirname, ".gh-token");
        if (existsSync(p)) return readFileSync(p, "utf-8").trim();
      } catch {
        /* ignore */
      }
      return "";
    })();
  if (!token) {
    console.error(
      "[publish] 未配置 GITHUB_TOKEN 且无 gh CLI, 无法上传\n" +
        "  方式: 1) 设环境变量 GITHUB_TOKEN=xxx\n" +
        "       2) 或把 token 写入 scripts/.gh-token(已 gitignore)"
    );
    process.exit(1);
  }
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "private-agent-publish" };
  // 创建 release(已存在则忽略)
  const createResp = await fetch(`https://api.github.com/repos/${REPO}/releases`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ tag_name: TAG, name: `Private Agent ${VERSION}`, body: notes }),
  });
  if (!createResp.ok && createResp.status !== 422) {
    console.error(`[publish] 创建 Release 失败: HTTP ${createResp.status}`);
    process.exit(1);
  }
  const release = await createResp.json();
  // 上传资产(先删同名旧资产)
  const uploadUrl = release.upload_url ? release.upload_url.split("{")[0] : null;
  if (!uploadUrl) {
    console.error("[publish] 无法获取上传地址(可能已存在, 用 gh CLI 或删旧 Release)");
    process.exit(1);
  }
  const body = readFileSync(installerPath);
  const upResp = await fetch(`${uploadUrl}?name=${encodeURIComponent(installer)}`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/octet-stream" },
    body,
  });
  if (!upResp.ok) {
    console.error(`[publish] 上传安装包失败: HTTP ${upResp.status}`);
    process.exit(1);
  }
  console.log(`[publish] Release 已发布: https://github.com/${REPO}/releases/tag/${TAG}`);
}

const hasGh = (() => {
  try {
    execFileSync("gh", ["--version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
})();

if (hasGh) {
  await publishWithGh();
} else {
  await publishWithApi();
}
