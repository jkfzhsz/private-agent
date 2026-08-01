"""蓝图 §2.13/§9.13 结构化 JSON 日志 + trace_id 预留。

B6.1:JSON 格式日志,每条含 timestamp/level/logger/message/trace_id。
蓝图 §9.13:trace_id_enabled: true(MVP 留空,默认 null)。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志格式化器(蓝图 §2.13)。

    输出格式:{"timestamp": "...", "level": "...", "logger": "...", "message": "...", "trace_id": null}
    trace_id 字段预留(蓝图 §9.13 MVP 留空,V2 从 contextvar 注入)。
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),  # MVP 默认 null
        }
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logger(
    name: str,
    stream: Any = None,
    level: int = logging.INFO,
    file_path: str | None = None,
) -> logging.Logger:
    """配置并返回结构化 JSON logger(蓝图 §2.13)。

    Args:
        name: logger 名称。
        stream: 输出流(默认 sys.stdout,蓝图 §9.13 stdout_enabled: true)。
        level: 日志级别(默认 INFO,蓝图 §9.13 level: "INFO")。
        file_path: 文件路径(非 None 时附加 FileHandler 写文件,B1 P1-2)。
            调用方负责 os.path.expandvars 展开环境变量。

    Returns:
        配置好的 logger(已添加 JSON handler)。
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    # 移除旧的 JSON handler(支持重新配置 stream/file,测试用)
    for h in logger.handlers[:]:
        if getattr(h, "_pa_json", False):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    # 添加 StreamHandler
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._pa_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    # 附加 FileHandler(B1 P1-2)
    if file_path is not None:
        parent = os.path.dirname(file_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler._pa_json = True  # type: ignore[attr-defined]
        logger.addHandler(file_handler)
    logger.propagate = False
    return logger
