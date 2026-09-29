"""P2 零硬编码守护（2026-09-29）: 运行期代码不得出现具体模型名字面量。

背景：模型迭代快、key 更迭频繁，硬编码的默认模型名会在换厂商/换模型后**静默
指向旧模型**。2026-09-29 实盘发现两处：

- `api/admin.py` 技能翻译兜底：读错链路径（`models.fallback_chain`，正确位置是
  `models.router.fallback_chain`）→ 恒空 → 恒定落到硬编码 `"deepseek-flash"`
- `tools/builtins/eval_runner.py`：非 mock 时固定 `model_id="deepseek-v4-flash"`

硬编码是"会复发的病"，用测试守住比靠人记可靠。

规则：扫描 `private_agent/**/*.py` 的字符串常量，命中"具体模型名形态"
（厂商前缀 + 分隔符 + 版本/型号后缀，如 `deepseek-flash` / `glm-5.2` /
`step-3.7-flash`）即失败。要求分隔符是为了排除普通词（如工具字段名 `"step"`）。

豁免：
- docstring（AST 层面排除）；
- 行内含 `# model-name-ok` 标注的字符串（示例文本、提示语等合法用途）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = BACKEND_ROOT / "private_agent"

#: 具体模型名形态: 厂商前缀 + 分隔符(-/_) + 版本或型号后缀。
MODEL_NAME_RE = re.compile(
    r"^(deepseek|glm|kimi|step|qwen|sensenova|seed|gpt|claude)[-_][a-z0-9._\-]+$",
    re.IGNORECASE,
)

ALLOW_MARKER = "model-name-ok"


def _docstring_ids(tree: ast.AST) -> set[int]:
    """收集模块/类/函数 docstring 的节点 id（这些字符串不算运行期默认值）。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """返回该文件中命中"疑似硬编码模型名"的 (行号, 值) 列表。"""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines = src.splitlines()
    docstrings = _docstring_ids(tree)

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue
        value = node.value.strip()
        if not MODEL_NAME_RE.match(value):
            continue
        line = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else ""
        if ALLOW_MARKER in line:
            continue
        hits.append((node.lineno, value))
    return sorted(hits)


def test_no_hardcoded_model_names_in_runtime_code():
    """运行期代码不得出现具体模型名字面量。

    需要写示例文本时，在行尾加 `# model-name-ok` 显式豁免（并写清理由）。
    """
    offenders: list[str] = []
    for path in sorted(PKG_ROOT.rglob("*.py")):
        for lineno, value in _scan_file(path):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{lineno}  {value!r}")

    assert not offenders, (
        "发现硬编码模型名 —— 运行期默认值应改为从 cfg / 模型链解析:\n  "
        + "\n  ".join(offenders)
    )


def test_scanner_detects_planted_offender(tmp_path):
    """守护测试自证: 扫描器对植入样本必须报错(防扫描逻辑失效导致假绿)。"""
    sample = tmp_path / "sample.py"
    sample.write_text('MODEL = "deepseek-v9-turbo"\n', encoding="utf-8")
    assert _scan_file(sample) == [(1, "deepseek-v9-turbo")]


def test_scanner_ignores_docstring_marker_and_plain_words(tmp_path):
    """docstring / `# model-name-ok` 标注行 / 无分隔符普通词 均应豁免。"""
    sample = tmp_path / "sample2.py"
    sample.write_text(
        '"""模块 docstring 提到 glm-5.2 只是说明文字。"""\n'
        'A = "deepseek-flash"  # model-name-ok 提示语示例\n'
        'B = "step"\n'
        'C = "kimi-k2.7-code"\n',
        encoding="utf-8",
    )
    # 只有 C 应被判定为违规
    assert _scan_file(sample) == [(4, "kimi-k2.7-code")]
