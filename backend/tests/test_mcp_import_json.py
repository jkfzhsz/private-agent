"""MCP JSON 导入认证解析测试(防止回归)。

覆盖: headers 认证提取的多种写法(裸 token / Bearer / Token / api-key)、
三种 JSON 形态(mcpServers / 数组 / 单对象)。
"""
from private_agent.api.admin import _extract_auth, _parse_mcp_json


class TestExtractAuth:
    def test_bare_token(self):
        """裸 token(无前缀)应原样提取 —— 用户实测场景。"""
        assert (
            _extract_auth({"Authorization": "eyJhbGciOiJSU0E.xxx.yyy"})
            == "eyJhbGciOiJSU0E.xxx.yyy"
        )

    def test_bearer_prefix(self):
        assert _extract_auth({"Authorization": "Bearer abc123"}) == "abc123"

    def test_case_insensitive_key(self):
        assert _extract_auth({"authorization": "Bearer xyz"}) == "xyz"

    def test_api_key_headers(self):
        assert _extract_auth({"x-api-key": "key-999"}) == "key-999"
        assert _extract_auth({"apikey": "key-888"}) == "key-888"

    def test_token_prefix(self):
        assert _extract_auth({"Authorization": "Token tkn1"}) == "tkn1"

    def test_empty_headers(self):
        assert _extract_auth({}) == ""
        assert _extract_auth(None) == ""


class TestParseMcpJson:
    def test_mcp_servers_with_bare_token(self):
        """mcpServers 格式: headers.Authorization 裸 token 应提取。"""
        result = _parse_mcp_json({
            "mcpServers": {
                "s1": {"url": "http://x/mcp", "headers": {"Authorization": "eyJraw.raw"}}
            }
        })
        assert result[0]["auth_token"] == "eyJraw.raw"
        assert result[0]["type"] == "http"

    def test_array_format_with_headers(self):
        """数组格式: headers.Authorization 也应提取。"""
        result = _parse_mcp_json([
            {"id": "s2", "type": "http", "url": "http://y/mcp",
             "headers": {"Authorization": "Bearer arr-token"}}
        ])
        assert result[0]["auth_token"] == "arr-token"

    def test_single_object_with_headers(self):
        """单个对象格式: 也应提取认证。"""
        result = _parse_mcp_json({
            "id": "s3", "url": "http://z/mcp",
            "headers": {"authorization": "Bearer obj-token"},
        })
        assert result[0]["auth_token"] == "obj-token"

    def test_stdio_from_mcp_servers(self):
        """stdio 类型: command/args 正确解析, 无 url。"""
        result = _parse_mcp_json({
            "mcpServers": {"s4": {"command": "npx", "args": ["-y", "pkg"]}}
        })
        assert result[0]["type"] == "stdio"
        assert result[0]["command"] == "npx"
        assert result[0]["args"] == ["-y", "pkg"]

    def test_no_headers_ok(self):
        """无 headers 时不应抛错。"""
        result = _parse_mcp_json({"mcpServers": {"s5": {"url": "http://w/mcp"}}})
        assert result[0].get("auth_token") is None
