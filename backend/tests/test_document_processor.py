"""蓝图 §4.7/§4.8 DocumentProcessor + chunking 策略测试。

纯函数测试,不依赖 DB。
"""
from __future__ import annotations

import pytest

from private_agent.knowledge.document_processor import (
    DocumentProcessor,
    _split_by_lines,
    _split_by_paragraph,
    chunk_code,
    chunk_markdown,
    chunk_pdf,
)


# ══════════════════════════════════════════════════════════════════════════
# DocumentProcessor.detect_type
# ══════════════════════════════════════════════════════════════════════════


class TestDetectType:
    def test_detect_markdown_by_ext(self):
        proc = DocumentProcessor()
        assert proc.detect_type("readme.md") == "markdown"
        assert proc.detect_type("CHANGELOG.markdown") == "markdown"

    def test_detect_markdown_by_content(self):
        proc = DocumentProcessor()
        assert proc.detect_type("unknown.txt", "# 标题") == "markdown"

    def test_detect_pdf(self):
        proc = DocumentProcessor()
        assert proc.detect_type("doc.pdf") == "pdf"

    def test_detect_code_py(self):
        proc = DocumentProcessor()
        assert proc.detect_type("main.py") == "code"

    def test_detect_code_js(self):
        proc = DocumentProcessor()
        assert proc.detect_type("app.js") == "code"

    def test_detect_code_ts(self):
        proc = DocumentProcessor()
        assert proc.detect_type("component.ts") == "code"

    def test_detect_code_java(self):
        proc = DocumentProcessor()
        assert proc.detect_type("Main.java") == "code"

    def test_detect_code_go(self):
        proc = DocumentProcessor()
        assert proc.detect_type("main.go") == "code"

    def test_detect_plain(self):
        proc = DocumentProcessor()
        assert proc.detect_type("notes.txt") == "plain"

    def test_detect_plain_no_ext(self):
        proc = DocumentProcessor()
        assert proc.detect_type("README") == "plain"


# ══════════════════════════════════════════════════════════════════════════
# DocumentProcessor.process (完整流水线)
# ══════════════════════════════════════════════════════════════════════════


class TestProcess:
    def test_process_markdown(self):
        proc = DocumentProcessor()
        content = "# 标题1\n\n正文内容1\n\n## 子标题\n\n正文内容2"
        chunks = proc.process(content, "doc.md", scenario="office")
        assert len(chunks) >= 2
        assert all(c.doc_type == "markdown" for c in chunks)
        assert all(c.scenario == "office" for c in chunks)
        assert all(c.source == "doc.md" for c in chunks)

    def test_process_code(self):
        proc = DocumentProcessor()
        content = "def foo():\n    pass\n\ndef bar():\n    return 42"
        chunks = proc.process(content, "utils.py")
        assert len(chunks) >= 2
        assert all(c.doc_type == "code" for c in chunks)

    def test_process_plain(self):
        proc = DocumentProcessor()
        content = "这是一段纯文本。\n\n没有特殊格式。\n\n只有段落。"
        chunks = proc.process(content, "notes.txt")
        assert len(chunks) >= 1
        assert all(c.doc_type == "plain" for c in chunks)

    def test_process_empty_content(self):
        proc = DocumentProcessor()
        chunks = proc.process("", "empty.txt")
        assert chunks == []

    def test_process_with_custom_params(self):
        params = {"markdown": {"chunk_size": 100, "chunk_overlap": 20}}
        proc = DocumentProcessor(chunk_params=params)
        # 长 markdown 内容
        content = "\n\n".join([f"# H{i}\n\n{'p' * 80}" for i in range(10)])
        chunks = proc.process(content, "doc.md")
        # 小 chunk_size 应该产生更多分块
        assert len(chunks) > 5


# ══════════════════════════════════════════════════════════════════════════
# DocumentProcessor.compute_hash
# ══════════════════════════════════════════════════════════════════════════


class TestComputeHash:
    def test_compute_hash_consistent(self):
        h1 = DocumentProcessor.compute_hash("hello world")
        h2 = DocumentProcessor.compute_hash("hello world")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_compute_hash_different(self):
        h1 = DocumentProcessor.compute_hash("hello")
        h2 = DocumentProcessor.compute_hash("world")
        assert h1 != h2

    def test_compute_hash_empty(self):
        h = DocumentProcessor.compute_hash("")
        assert len(h) == 64


# ══════════════════════════════════════════════════════════════════════════
# chunk_markdown
# ══════════════════════════════════════════════════════════════════════════


class TestChunkMarkdown:
    def test_simple_headings(self):
        content = "# Title\n\nContent.\n\n## Sub\n\nMore content."
        chunks = chunk_markdown(content)
        assert len(chunks) >= 2
        # 第一个 chunk 有标题 metadata
        assert chunks[0][1].get("title") == "Title"
        assert chunks[0][1].get("level") == 1

    def test_large_section_split(self):
        content = "# Big\n\n" + "\n\n".join(["p" * 300] * 10)
        chunks = chunk_markdown(content, chunk_size=200, overlap=20)
        assert len(chunks) >= 2

    def test_empty_content(self):
        chunks = chunk_markdown("")
        assert chunks == []

    def test_no_headings(self):
        content = "Just plain text.\n\nWith paragraphs."
        chunks = chunk_markdown(content)
        # 无标题时,整体作为一块
        assert len(chunks) == 1
        assert chunks[0][1].get("title") == ""


# ══════════════════════════════════════════════════════════════════════════
# chunk_pdf
# ══════════════════════════════════════════════════════════════════════════


class TestChunkPdf:
    def test_simple_paragraphs(self):
        content = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3."
        chunks = chunk_pdf(content)
        assert len(chunks) >= 1
        assert all(c[1]["type"] == "pdf" for c in chunks)

    def test_empty_content(self):
        chunks = chunk_pdf("")
        assert chunks == []

    def test_large_paragraph_split(self):
        content = "\n\n".join(["p" * 400] * 10)
        chunks = chunk_pdf(content, chunk_size=200, overlap=20)
        assert len(chunks) >= 2


# ══════════════════════════════════════════════════════════════════════════
# chunk_code
# ══════════════════════════════════════════════════════════════════════════


class TestChunkCode:
    def test_function_boundaries(self):
        content = "def foo():\n    pass\n\ndef bar():\n    return 42"
        chunks = chunk_code(content, "test.py")
        assert len(chunks) >= 2
        assert chunks[0][1].get("file") == "test.py"

    def test_class_boundary(self):
        content = "class MyClass:\n    pass\n\ndef helper():\n    return 1"
        chunks = chunk_code(content, "test.py")
        assert len(chunks) >= 2

    def test_symbol_metadata(self):
        content = "def hello():\n    print('hi')"
        chunks = chunk_code(content, "test.py")
        assert chunks[0][1].get("symbol") == "hello"

    def test_empty_content(self):
        chunks = chunk_code("", "empty.py")
        assert chunks == []

    def test_large_function_split(self):
        content = "def big():\n" + "\n".join(["    print(i)" for i in range(100)])
        chunks = chunk_code(content, "test.py", chunk_size=100, overlap=10)
        assert len(chunks) >= 2


# ══════════════════════════════════════════════════════════════════════════
# _split_by_paragraph
# ══════════════════════════════════════════════════════════════════════════


class TestSplitByParagraph:
    def test_no_split_needed(self):
        result = _split_by_paragraph("Short text.", 1000, 100)
        assert len(result) == 1
        assert result[0] == "Short text."

    def test_split_into_multiple(self):
        text = "\n\n".join(["p" * 300] * 5)
        result = _split_by_paragraph(text, 200, 20)
        assert len(result) >= 2

    def test_empty_text(self):
        result = _split_by_paragraph("", 100, 10)
        assert result == []


# ══════════════════════════════════════════════════════════════════════════
# _split_by_lines
# ══════════════════════════════════════════════════════════════════════════


class TestSplitByLines:
    def test_no_split(self):
        result = _split_by_lines("line1\nline2\n", 1000, 100)
        assert len(result) == 1

    def test_split_into_multiple(self):
        lines = "\n".join([f"line{i}" for i in range(50)])
        result = _split_by_lines(lines, 100, 20)
        assert len(result) >= 2

    def test_empty_text(self):
        result = _split_by_lines("", 100, 10)
        assert result == []