# -*- coding: utf-8 -*-
"""为 PA 配置智谱 GLM-4.6V-Flash 视觉 provider（modlens 成功经验落地）。

- 新增 provider: glm-vision (base_url=智谱官方 v4, model=glm-4.6v-flash, multimodal=true)
- 配置 models.router.vision_chain = ["glm-vision"]（发图语境自动切换）
- 不触碰 fallback_chain / text_chain（纯文本链路保持现状）
- API key 从 modlens 配置复用, AES-256-GCM 加密存 config_runtime
"""
import asyncio
import json
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from private_agent.config.secrets import encrypt_api_key  # noqa: E402

PROVIDER = "glm-vision"
BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
MODEL = "glm-4.6v-flash"


async def main() -> None:
    api_key = (
        json.load(open(r"C:\Users\zongxin\.modlens\config.json", encoding="utf-8"))
        .get("providers", {})
        .get("openai", {})
        .get("apiKey")
    )
    if not api_key:
        raise SystemExit("modlens config 中未找到 apiKey")
    # 2026-08-14 修复: master key 必须与生产后端一致 —— Electron 加载
    # %APPDATA%/Private Agent/backend.env 优先(run_sidecar 的
    # _restore_keys_from_runtime 用它解密)。旧实现读 backend/.env 的
    # master 加密 → 与生产 master 不匹配 → restore 解密失败 → 401
    # "令牌已过期"(deepseek 等 provider 用生产 master 加密, 正常)。
    master_hex = ""
    user_env = os.path.join(
        os.environ.get("APPDATA", ""), "Private Agent", "backend.env"
    )
    if os.path.exists(user_env):
        for line in open(user_env, encoding="utf-8"):
            if line.startswith("PA_MASTER_KEY="):
                master_hex = line.strip().split("=", 1)[1]
                break
    if not master_hex:
        master_hex = os.environ.get("PA_MASTER_KEY", "")
    if not master_hex:
        raise SystemExit("未找到 PA_MASTER_KEY(backend.env 优先, 回退 backend/.env)")
    master = bytes.fromhex(master_hex)
    encrypted = encrypt_api_key(api_key, master)

    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres",
        password=os.environ["PA_DB_PASSWORD"], database="private_agent",
    )
    try:
        prefix = f"models.providers.{PROVIDER}"

        async def set_runtime(key: str, val) -> None:
            await conn.execute(
                "INSERT INTO config_runtime (key, value) VALUES ($1, $2::jsonb) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                key, json.dumps(val, ensure_ascii=False),
            )

        await set_runtime(f"{prefix}.base_url", BASE_URL)
        await set_runtime(f"{prefix}.model_name", MODEL)
        await set_runtime(f"{prefix}.enabled", True)
        await set_runtime(f"{prefix}.kind", "cloud")
        await set_runtime(f"{prefix}.multimodal", True)
        await set_runtime(f"{prefix}.api_key_encrypted", encrypted)
        await set_runtime("models.router.vision_chain", [PROVIDER])

        # 校验
        rows = await conn.fetch(
            "SELECT key, value FROM config_runtime "
            "WHERE key LIKE $1 OR key = 'models.router.vision_chain' ORDER BY key",
            f"{prefix}.%",
        )
        for r in rows:
            v = r["value"]
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            if "key_encrypted" in r["key"]:
                v = {k: (vv[:12] + "..." if len(vv) > 12 else vv) for k, vv in v.items()}
            print(f"{r['key']} = {json.dumps(v, ensure_ascii=False)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
