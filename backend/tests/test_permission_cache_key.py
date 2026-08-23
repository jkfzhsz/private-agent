"""M3 权限缓存 cache_key 函数测试(蓝图 §7.5,spec AC-4/5)。

Source: plan/m3-remaining-done-criteria step 5
- get_permission_cache_key(skill_name, tool_name, args) 返回 64 字符 sha256 hex
- 不同 skill_name 同 tool 同 args → 不同 cache_key
- 同输入 → 相同 cache_key(幂等)
- args 字典键顺序不影响结果(sort_keys=True)
"""
from private_agent.tools.permission import get_permission_cache_key


class TestPermissionCacheKey:
    """AC-4/5: cache_key 含 skill_name,sha256 hex,跨 Skill 隔离。"""

    def test_returns_64_char_sha256_hex(self):
        """AC-4: 返回 64 字符 sha256 hex 字符串。"""
        key = get_permission_cache_key("office", "file_read", {"path": "/a/b.txt"})
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_different_skill_name_produces_different_key(self):
        """AC-5: 不同 skill_name 同 tool 同 args → 不同 cache_key。"""
        args = {"path": "/data/sales.xlsx"}
        key_office = get_permission_cache_key("office", "file_read", args)
        key_data = get_permission_cache_key("data_analysis", "file_read", args)
        key_frontend = get_permission_cache_key("frontend_design", "file_read", args)
        assert key_office != key_data
        assert key_office != key_frontend
        assert key_data != key_frontend

    def test_same_input_produces_same_key(self):
        """幂等性:同 skill + 同 tool + 同 args → 相同 cache_key。"""
        args = {"path": "/a/b.txt", "max_lines": 1000}
        key1 = get_permission_cache_key("office", "file_read", args)
        key2 = get_permission_cache_key("office", "file_read", args)
        assert key1 == key2

    def test_args_key_order_does_not_matter(self):
        """args 字典键顺序不同 → 相同 cache_key(sort_keys=True)。"""
        args1 = {"path": "/a", "max_lines": 100}
        args2 = {"max_lines": 100, "path": "/a"}
        key1 = get_permission_cache_key("office", "file_read", args1)
        key2 = get_permission_cache_key("office", "file_read", args2)
        assert key1 == key2

    def test_different_tool_name_produces_different_key(self):
        """不同 tool_name → 不同 cache_key。"""
        args = {"path": "/a"}
        key_read = get_permission_cache_key("office", "file_read", args)
        key_write = get_permission_cache_key("office", "file_write", args)
        assert key_read != key_write

    def test_different_args_produce_different_key(self):
        """不同 args → 不同 cache_key。"""
        key1 = get_permission_cache_key("office", "file_read", {"path": "/a"})
        key2 = get_permission_cache_key("office", "file_read", {"path": "/b"})
        assert key1 != key2

    def test_empty_args(self):
        """空 args 字典 → 仍返回有效 64 字符 hex。"""
        key = get_permission_cache_key("office", "file_read", {})
        assert len(key) == 64
