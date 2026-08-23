"""测试 datetime 内置工具。"""
from __future__ import annotations

import pytest

from private_agent.tools.builtins.datetime import datetime_handler


class TestDatetime:
    """datetime 工具:返回当前 ISO 8601 时间。"""

    async def test_returns_iso_format(self) -> None:
        result = await datetime_handler({})
        assert result.error is None
        assert "T" in result.output
        assert result.output.endswith("Z") or "+" in result.output or "-" in result.output[-6:]

    async def test_returns_current_year(self) -> None:
        from datetime import datetime, timezone
        result = await datetime_handler({})
        current_year = str(datetime.now(timezone.utc).year)
        assert current_year in result.output