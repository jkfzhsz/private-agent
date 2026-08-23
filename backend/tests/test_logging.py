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


# B1 P0-2: setup_logger 文件通道测试(AC-4, AC-5, AC-6)


def test_setup_logger_with_file_path(tmp_path):
    """AC-4: setup_logger(name, file_path=...) 后 logger.info → 文件含 JSON 行。"""
    log_file = tmp_path / "agent.log"
    logger = pa_logging.setup_logger("test_file_logger", file_path=str(log_file))
    logger.info("hello file")

    # FileHandler 可能缓冲,关闭所有 handler 刷盘
    for h in logger.handlers[:]:
        h.flush()
        h.close()
        logger.removeHandler(h)

    content = log_file.read_text(encoding="utf-8").strip()
    assert content, "log file should not be empty"
    data = json.loads(content)
    assert data["message"] == "hello file"
    assert data["level"] == "INFO"


def test_setup_logger_creates_parent_dir(tmp_path):
    """AC-5: file_path 父目录不存在 → os.makedirs 自动创建。"""
    log_file = tmp_path / "nonexistent" / "sub" / "agent.log"
    # 父目录不存在
    assert not log_file.parent.exists()

    logger = pa_logging.setup_logger("test_mkdir_logger", file_path=str(log_file))
    logger.info("auto mkdir")

    for h in logger.handlers[:]:
        h.flush()
        h.close()
        logger.removeHandler(h)

    assert log_file.exists(), "parent dir should be auto-created"
    content = log_file.read_text(encoding="utf-8").strip()
    assert json.loads(content)["message"] == "auto mkdir"


def test_setup_logger_without_file_path_only_stream():
    """AC-6: setup_logger(name) 不传 file_path → 仅 StreamHandler,无 FileHandler。"""
    import io

    buf = io.StringIO()
    logger = pa_logging.setup_logger("test_stream_only", stream=buf)
    logger.info("stream only")

    # 验证 handler 类型:有 StreamHandler,无 FileHandler
    has_stream = any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
    has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    assert has_stream, "should have StreamHandler"
    assert not has_file, "should NOT have FileHandler when file_path is None"

    # 清理
    for h in logger.handlers[:]:
        h.close()
        logger.removeHandler(h)
