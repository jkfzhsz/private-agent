"""阶段三批次 1 - 权限规则 DSL 单测(调研 round2 §4.2.1, AC-1~AC-4 骨架)。

覆盖:
- parse_rule: 合法/非法格式解析
- match_rule: 工具名精确/通配/specifier 参数模式
- evaluate_rules: deny 优先 / source 优先级 / 无匹配回退
"""
import pytest

from private_agent.tools.permission import (
    PermissionRule,
    evaluate_rules,
    match_rule,
    parse_rule,
)


class TestParseRule:
    """规则字符串解析。"""

    def test_parse_bare_tool(self):
        """无 specifier: "deny:code_execution"。"""
        r = parse_rule("deny:code_execution")
        assert r.action == "deny"
        assert r.tool == "code_execution"
        assert r.specifier is None
        assert r.source == "config"

    def test_parse_with_specifier(self):
        """带 specifier: "allow:file_write(//sandbox/**)"。"""
        r = parse_rule("allow:file_write(//sandbox/**)")
        assert r.action == "allow"
        assert r.tool == "file_write"
        assert r.specifier == "//sandbox/**"

    def test_parse_wildcard_tool(self):
        """工具名通配: "ask:mcp__iFind__*"。"""
        r = parse_rule("ask:mcp__iFind__*")
        assert r.tool == "mcp__iFind__*"

    def test_parse_custom_source(self):
        """自定义 source: skill。"""
        r = parse_rule("deny:http_request", source="skill")
        assert r.source == "skill"

    def test_parse_whitespace_tolerant(self):
        """容忍空白: " ask : http_request "。"""
        r = parse_rule(" ask : http_request ")
        assert r.action == "ask"
        assert r.tool == "http_request"

    def test_parse_invalid_action(self):
        """非法 action → ValueError。"""
        with pytest.raises(ValueError):
            parse_rule("grant:file_read")

    def test_parse_missing_colon(self):
        """缺少 action 前缀 → ValueError。"""
        with pytest.raises(ValueError):
            parse_rule("file_read")

    def test_parse_invalid_source(self):
        """非法 source → ValueError。"""
        with pytest.raises(ValueError):
            parse_rule("deny:file_read", source="admin")

    def test_parse_invalid_tool_name(self):
        """非法工具名(含空格/非法字符)→ ValueError。"""
        with pytest.raises(ValueError):
            parse_rule("deny:file read")


class TestMatchRule:
    """规则命中判定。"""

    def test_exact_tool_match(self):
        r = parse_rule("deny:code_execution")
        assert match_rule(r, "code_execution", {}) is True
        assert match_rule(r, "file_read", {}) is False

    def test_wildcard_tool_match(self):
        r = parse_rule("ask:mcp__iFind__*")
        assert match_rule(r, "mcp__iFind__get_stock_summary", {}) is True
        assert match_rule(r, "mcp__other__tool", {}) is False

    def test_specifier_matches_args_value(self):
        """specifier 对 args 字符串值 fnmatch: 路径命中。"""
        r = parse_rule("deny:file_write(//**/.env)")
        assert match_rule(r, "file_write", {"path": "//workspace//a/.env"}) is True
        assert match_rule(r, "file_write", {"path": "//workspace//a/b.txt"}) is False

    def test_specifier_no_spec_matches_any_args(self):
        """无 specifier 时仅按工具名匹配(任意 args)。"""
        r = parse_rule("deny:code_execution")
        assert match_rule(r, "code_execution", {"code": "x = 1"}) is True
        assert match_rule(r, "code_execution", {}) is True

    def test_specifier_matches_any_arg_key(self):
        """specifier 命中任一 args 字段即可(fnmatch 整串匹配)。"""
        r = parse_rule("deny:http_request(*api.internal*)")
        assert (
            match_rule(r, "http_request", {"url": "https://api.internal/x", "method": "GET"})
            is True
        )
        assert (
            match_rule(r, "http_request", {"url": "https://api.public.com/x"})
            is False
        )

    def test_non_string_args_ignored_by_specifier(self):
        """非字符串 args(数字/布尔)不参与 specifier 匹配。"""
        r = parse_rule("deny:code_execution(secret*)")
        assert match_rule(r, "code_execution", {"code": 123, "timeout": 10}) is False


class TestEvaluateRules:
    """规则集求值: deny 优先 + source 优先级。"""

    def test_empty_rules_returns_none(self):
        """空规则集 → None(回退 safety_level)。"""
        assert evaluate_rules([], "file_read", {}) is None

    def test_no_match_returns_none(self):
        """规则存在但都不匹配 → None。"""
        rules = [parse_rule("deny:code_execution")]
        assert evaluate_rules(rules, "file_read", {}) is None

    def test_deny_wins_over_allow(self):
        """deny 优先于一切 allow(同工具)。"""
        rules = [
            parse_rule("allow:code_execution"),
            parse_rule("deny:code_execution"),
        ]
        assert evaluate_rules(rules, "code_execution", {}) == "deny"

    def test_deny_wins_across_sources(self):
        """高优先级 source 的 allow 也不能压过低优先级 source 的 deny。"""
        rules = [
            parse_rule("deny:http_request", source="config"),
            parse_rule("allow:http_request", source="session"),
        ]
        assert evaluate_rules(rules, "http_request", {}) == "deny"

    def test_session_beats_skill_beats_config(self):
        """非 deny 决策按 source 优先级: session > skill > config。"""
        rules = [
            parse_rule("ask:file_write", source="config"),
            parse_rule("allow:file_write", source="skill"),
            parse_rule("deny:file_write", source="session"),
        ]
        assert evaluate_rules(rules, "file_write", {"path": "/a"}) == "deny"

    def test_higher_source_wins_non_deny(self):
        """无 deny 时, 高优先级 source 的决策胜出。"""
        rules = [
            parse_rule("ask:file_write", source="skill"),
            parse_rule("allow:file_write", source="session"),
        ]
        assert evaluate_rules(rules, "file_write", {}) == "allow"

    def test_first_match_within_same_source(self):
        """同一 source 内取首个匹配(列表顺序)。"""
        rules = [
            parse_rule("ask:file_read", source="config"),
            parse_rule("allow:file_write", source="config"),
        ]
        # file_read 匹配第一条 → ask
        assert evaluate_rules(rules, "file_read", {}) == "ask"
        # file_write 只匹配第二条 → allow
        assert evaluate_rules(rules, "file_write", {}) == "allow"

    def test_specifier_narrowing_deny(self):
        """specifier 收窄: 仅命中特定参数的 deny。"""
        rules = [
            parse_rule("deny:file_write(//**/.env)", source="config"),
            parse_rule("allow:file_write", source="config"),
        ]
        # 写 .env → deny(deny 优先)
        assert (
            evaluate_rules(rules, "file_write", {"path": "//x/.env"}) == "deny"
        )
        # 写普通文件 → 无 deny 命中, allow 生效
        assert (
            evaluate_rules(rules, "file_write", {"path": "//x/a.txt"}) == "allow"
        )
