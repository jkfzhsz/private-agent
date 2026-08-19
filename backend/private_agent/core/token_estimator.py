"""蓝图 §3.14 TokenEstimator — 字符数/token 估算(3.0 字符/token 兜底)。

B4 P0-1: 供 Compressor(压缩触发)和 BillingRecorder(计费)共用。
V2 升级: 注册 tiktoken / 模型专属 tokenizer。
"""
from __future__ import annotations

import json


class TokenEstimator:
    CHARS_PER_TOKEN = 3.0

    def estimate(self, text: str, model_id: str | None = None) -> int:
        return max(1, int(len(text) / self.CHARS_PER_TOKEN))

    def estimate_messages(self, messages: list[dict], model_id: str | None = None) -> int:
        total = 0
        for m in messages:
            if m.get("compressed"):
                continue
            content = m.get("content", "") or ""
            total += self.estimate(content, model_id)
            for tc in m.get("tool_calls", []):
                total += self.estimate(json.dumps(tc), model_id)
        return total