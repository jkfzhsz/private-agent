"""蓝图 §2.13/§9.13 结构化 JSON 日志 + trace_id 预留。

B6.1:JSON 格式日志,每条含 timestamp/level/logger/message/trace_id。
蓝图 §9.13:trace_id_enabled: true(MVP 留空,默认 null)。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# 蓝图 §2.13 JSON 日志必需字段
_REQUIRED_FIELDS = ("timestamp", "level", "logger", "message", "trace_id")


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
) -> logging.Logger:
    """配置并返回结构化 JSON logger(蓝图 §2.13)。

    Args:
        name: logger 名称。
        stream: 输出流(默认 sys.stdout,蓝图 §9.13 stdout_enabled: true)。
        level: 日志级别(默认 INFO,蓝图 §9.13 level: "INFO")。

    Returns:
        配置好的 logger(已添加 JSON handler)。
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    # 移除旧的 JSON handler(支持重新配置 stream,测试用)
    for h in logger.handlers[:]:
        if getattr(h, "_pa_json", False):
            logger.removeHandler(h)
    # 添加新 handler
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._pa_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False
    return logger
