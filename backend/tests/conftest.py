"""全局测试夹具。

阶段二批次 1: admin 鉴权生效后, 走 main.app 的测试需注入 PA_ADMIN_TOKEN,
否则 43 个控制面端点全部 401。autouse 保证所有测试默认通过鉴权。
"""
import pytest

TEST_ADMIN_TOKEN = "test-admin-token"


@pytest.fixture(autouse=True)
def _admin_token_env(monkeypatch):
    """默认注入 admin token(测试专用; 生产 token 由 run_sidecar ensure)。"""
    monkeypatch.setenv("PA_ADMIN_TOKEN", TEST_ADMIN_TOKEN)
