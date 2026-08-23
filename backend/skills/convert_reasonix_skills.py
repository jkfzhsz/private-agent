"""一次性转换脚本: reasonix-skills → PA 原生 Skill 格式。

来源: backend/skills/reasonix-skills-main.zip(社区 17 技能)
排除: subagent-dev(子代理类)、use-skills(重度依赖 run_skill/ask_choice 组合机制)
输出: backend/skills/{name}/ 目录式(skill.yaml + system_prompt.md + tools.yaml),
      与 office/data_analysis/frontend_design 同构, PA 现有模式技能机制直接加载。

模型限定: 生成的 skill.yaml 带 model_scope: ["deepseek"] —— 仅 DeepSeek 系列
会话默认使用(前端技能面板按会话模型过滤/默认选中; PA 侧消费点见设计文档)。

用法: python convert_reasonix_skills.py [--zip path] [--out dir]
"""
from __future__ import annotations

import argparse
import datetime
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

# ── 选中清单(排除 subagent-dev / use-skills) ─────────────────────────────
SELECTED = {
    "documents": ["docx", "pptx", "xlsx", "pdf"],
    "engineering": ["tdd", "systematic-debug", "git-worktree"],
    "meta": ["search-first", "prompts-chat-guide"],
    "writing": [
        "article-writer", "novelist", "writing-humanizer",
        "doc-coauthor", "duan-nian-jian", "novel-workflow",
    ],
}

# ── 分类 → 工具依赖 / 权限 ────────────────────────────────────────────────
CAT_TOOLS = {
    "documents": [
        ("code_execution", "safe"),
        ("file_read", "safe"),
        ("file_write", "elevated"),
    ],
    "engineering": [
        ("code_execution", "elevated"),
        ("file_read", "safe"),
        ("file_write", "elevated"),
    ],
    "writing": [
        ("code_execution", "safe"),
        ("file_read", "safe"),
        ("file_write", "elevated"),
    ],
    "meta": [
        ("web_search", "safe"),
        ("http_request", "elevated"),
    ],
}

CAT_PERMS = {
    "documents": {"allow_file_write": True, "allow_network": True, "sandbox_enabled": True},
    "engineering": {"allow_file_write": True, "allow_network": False, "sandbox_enabled": True},
    "writing": {"allow_file_write": True, "allow_network": False, "sandbox_enabled": True},
    "meta": {"allow_file_write": False, "allow_network": True, "sandbox_enabled": True},
}

CAT_LABEL = {
    "documents": "文档生成",
    "engineering": "工程开发",
    "writing": "写作创作",
    "meta": "通用策略",
}

# Reasonix 私有机制引用(转换时从 body 移除/改写, PA 无对应消费点)
REASONIX_REF_PATTERNS = [
    re.compile(r"`?/skill`?"),               # /skill 斜杠命令
    re.compile(r"`?run_skill\b`?"),          # run_skill 工具
    re.compile(r"`?ask_choice\b`?"),         # ask_choice 工具
    re.compile(r"`?read_only_skill\b`?"),    # read_only_skill
    re.compile(r"\bsubagent\b"),             # subagent 概念(保留 body 其余内容)
    re.compile(r"`?reasonix\b`?", re.I),     # reasonix 引擎名
]


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 `---` YAML frontmatter + body。返回 (fm_dict, body)。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.S)
    if not m:
        return {}, content
    fm_text, body = m.group(1), m.group(2)
    fm: dict = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip().strip("\"'")
    return fm, body.strip()


def clean_body(body: str) -> str:
    """清洗 Reasonix 私有引用, 保留方法论主体。"""
    out = body
    for pat in REASONIX_REF_PATTERNS:
        out = pat.sub("", out)
    # 收敛多余空行
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def gen_skill_yaml(name: str, desc: str, category: str) -> str:
    tools = CAT_TOOLS.get(category, [])
    perms = CAT_PERMS.get(category, {})
    dep_lines = "\n".join(
        f"    - name: {t}\n      safety_level_override: {s}"
        for t, s in tools
    )
    perm_lines = "\n".join(f"  {k}: {str(v).lower()}" for k, v in perms.items())
    return f"""name: {name}
version: "1.0.0"
description: "{desc}"
scenario: {category}
author: "reasonix-community"
created_at: "{datetime.date.today().isoformat()}"
enabled: true
model_scope: ["deepseek"]

dependencies:
  tools:
{dep_lines}

permissions:
{perm_lines}
  max_file_size_mb: 50

knowledge_base:
  enabled: false
  scenario: {category}
  auto_retrieve: false

examples:
  enabled: false
  max_examples: 0
  inject_to: frozen_zone

max_frozen_token: 4000
"""


def gen_tools_yaml(tools: list[tuple[str, str]]) -> str:
    lines = []
    for t, s in tools:
        lines.append(f"- name: {t}\n  safety_level_override: {s}\n  enabled: true")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default="reasonix-skills-main.zip")
    ap.add_argument("--out", default=".")
    args = ap.parse_args()

    zip_path = Path(args.zip)
    out_root = Path(args.out)
    if not zip_path.exists():
        print(f"[ERR] zip 不存在: {zip_path}")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="reasonix-conv-"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        src_root = tmp / "reasonix-skills-main"

        converted, skipped, failed = [], [], []
        for category, names in SELECTED.items():
            for name in names:
                md = src_root / category / f"{name}.md"
                if not md.exists():
                    skipped.append(f"{name}(源文件缺失)")
                    continue
                content = md.read_text(encoding="utf-8")
                fm, body = parse_frontmatter(content)
                desc = fm.get("description", f"{CAT_LABEL[category]}技能 {name}")
                cleaned = clean_body(body)
                if not cleaned:
                    skipped.append(f"{name}(body 为空)")
                    continue

                skill_dir = out_root / name
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "skill.yaml").write_text(
                    gen_skill_yaml(name, desc, category), encoding="utf-8"
                )
                (skill_dir / "system_prompt.md").write_text(cleaned, encoding="utf-8")
                (skill_dir / "tools.yaml").write_text(
                    gen_tools_yaml(CAT_TOOLS.get(category, [])), encoding="utf-8"
                )
                converted.append(name)

        print(f"== 转换完成: {len(converted)} 成功 ==")
        for name in converted:
            print(f"  ✓ {name}")
        if skipped:
            print(f"== 跳过/失败: {len(skipped)} ==")
            for s in skipped:
                print(f"  ✗ {s}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
