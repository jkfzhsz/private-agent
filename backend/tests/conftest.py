"""全局测试夹具。

阶段二批次 1: admin 鉴权生效后, 走 main.app 的测试需注入 PA_ADMIN_TOKEN,
否则 43 个控制面端点全部 401。autouse 保证所有测试默认通过鉴权。

2026-08-06: 隔离 APPDATA —— _ensure_master_key 等会把 PA_MASTER_KEY 写入
%APPDATA%/Private Agent/backend.env, 不隔离会污染真实用户配置。
"""
import pytest

TEST_ADMIN_TOKEN = "test-admin-token"


@pytest.fixture(autouse=True)
def _admin_token_env(monkeypatch):
    """默认注入 admin token(测试专用; 生产 token 由 run_sidecar ensure)。"""
    monkeypatch.setenv("PA_ADMIN_TOKEN", TEST_ADMIN_TOKEN)


@pytest.fixture(autouse=True)
def _embedding_mock_env(monkeypatch):
    """0.5.1: 强制 embedding mock 模式 —— factory.build_embedding_service
    检查 PA_EMBEDDING_MOCK=1 → worker_pool=None(全 0 mock 分支)。
    任何测试误触 KB 装配也不会加载真实 bge 模型(防内存暴涨/用例超时);
    真实链路验证由手动脚本/实际运行路径触发(PA_EMBEDDING_MOCK 不设)。"""
    monkeypatch.setenv("PA_EMBEDDING_MOCK", "1")


@pytest.fixture(autouse=True)
def _isolate_appdata(monkeypatch, tmp_path_factory):
    """APPDATA 指向 session 级临时目录: 用户配置写文件类操作(backend.env /
    master key)不污染真实用户目录。APPDATA 跨测试稳定 → master key 继承稳定。"""
    appdata = tmp_path_factory.mktemp("pa-appdata")
    monkeypatch.setenv("APPDATA", str(appdata))
