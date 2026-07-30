"""B6.1 - 结构化 JSON 日志 + trace_id 预留。

Source: plan/m0-implementation step 6 (蓝图 §9.6 step6 + §2.13 + §9.13 observability)

蓝图 §2.13:结构化 JSON 日志,每条含 timestamp/level/logger/message/trace_id。
蓝图 §9.13:trace_id_enabled: true(MVP 留空)。
"""
import io
import json
import logging

from private_agent.observability import logging as pa_logging


def _capture_logger_log(message: str, level: int = logging.INFO) -> dict:
    """配置 logger 输出到 StringIO,返回解析后的 JSON dict。"""
    buf = io.StringIO()
    logger = pa_logging.setup_logger("test_logger", stream=buf, level=level)
    logger.info(message)
    line = buf.getvalue().strip()
    return json.loads(line)


def test_log_output_is_valid_json():
    """日志输出是可解析的 JSON。"""
    data = _capture_logger_log("hello world")
    assert isinstance(data, dict)


def test_log_has_timestamp():
    """JSON 日志含 timestamp 字段。"""
    data = _capture_logger_log("test")
    assert "timestamp" in data


def test_log_has_level():
    """JSON 日志含 level 字段,值为 INFO。"""
    data = _capture_logger_log("test")
    assert data["level"] == "INFO"


def test_log_has_message():
    """JSON 日志含 message 字段。"""
    data = _capture_logger_log("hello world")
    assert data["message"] == "hello world"


def test_log_has_trace_id_field_default_null():
    """JSON 日志含 trace_id 字段,MVP 默认为 null(蓝图 §9.13 trace_id_enabled 但留空)。"""
    data = _capture_logger_log("test")
    assert "trace_id" in data
    assert data["trace_id"] is None
