"""M4 §8.14 eval/rollback.py - 三类载体回滚机制(蓝图 §8.14)。

Source: plan/m4-version-compare-rollback step 2
- SkillRollbackManager: prompt / skill / harness 三类回滚
- VersionNotFoundError: 回滚版本不存在异常
- 回滚仅对新会话生效,运行中会话维持 locked_skill_version(M3 已实现)
- config_runtime 表 key=`skill.{name}.latest_version` 作为新会话加载版本指针
"""
from __future__ import annotations

import json
from typing import Any

import asyncpg

from private_agent.eval.repos import VersionSnapshotRepo

__all__ = ["SkillRollbackManager", "VersionNotFoundError"]


class VersionNotFoundError(Exception):
    """回滚目标版本不存在异常(蓝图 §8.14)。"""


class SkillRollbackManager:
    """三类载体回滚管理器(蓝图 §8.14,AC-3, AC-4, AC-5)。

    - rollback_prompt: 仅回滚 Prompt,不影响工具白名单
    - rollback_skill: 回滚整个 Skill(元数据 + Prompt + 工具白名单)
    - rollback_harness: 返回 git revert 命令(不自动执行)

    回滚仅对新会话生效,运行中会话维持 sessions.locked_skill_version(M3)。
    """

    def __init__(
        self,
        snapshot_repo: VersionSnapshotRepo,
        skill_loader: Any = None,
        skill_repo: Any = None,
    ) -> None:
        self._snapshot_repo = snapshot_repo
        self._skill_loader = skill_loader
        self._skill_repo = skill_repo

    async def rollback_prompt(
        self,
        *,
        skill_name: str,
        target_version: str,
        conn: asyncpg.Connection,
    ) -> dict:
        """仅回滚 Prompt(AC-3)。

        1. snapshot_repo.get(scope="prompt", version=target_version) 读历史 Prompt
        2. UPDATE skills 表 system_prompt(不动 version / tools)
        3. 仅对新会话生效,运行中会话维持锁定版本

        Returns:
            {rolled_back_to, scope: "prompt", affected_sessions: 0}
        """
        payload = await self._snapshot_repo.get(
            scope="prompt", version=target_version
        )
        if payload is None:
            raise VersionNotFoundError(
                f"Prompt 版本 '{target_version}' 不存在(version_snapshots 无记录)"
            )
        new_prompt = payload.get("system_prompt", "")
        await conn.execute(
            "UPDATE skills SET system_prompt=$2, updated_at=now() WHERE name=$1",
            skill_name,
            new_prompt,
        )
        # 更新 latest_prompt_version 指针(config_runtime)
        await self._upsert_config_runtime(
            conn,
            key=f"skill.{skill_name}.latest_prompt_version",
            value=target_version,
        )
        return {
            "rolled_back_to": target_version,
            "scope": "prompt",
            "affected_sessions": 0,
        }

    async def rollback_skill(
        self,
        *,
        skill_name: str,
        target_version: str,
        conn: asyncpg.Connection,
    ) -> dict:
        """回滚整个 Skill(AC-4)。

        1. snapshot_repo.get(scope="skill", version=target_version) 读历史 Skill 快照
        2. UPDATE skills 表 version + manifest + system_prompt + tools
        3. UPDATE config_runtime latest_version 指针(新会话加载 target_version)

        Returns:
            {rolled_back_to, scope: "skill", affected_sessions: 0}
        """
        payload = await self._snapshot_repo.get(
            scope="skill", version=target_version
        )
        if payload is None:
            raise VersionNotFoundError(
                f"Skill 版本 '{target_version}' 不存在(version_snapshots 无记录)"
            )
        manifest = payload.get("manifest", {})
        if isinstance(manifest, str):
            manifest = json.loads(manifest)
        system_prompt = payload.get("system_prompt", "")
        tools_yaml = payload.get("tools_yaml", [])
        if isinstance(tools_yaml, str):
            tools_yaml = json.loads(tools_yaml)
        await conn.execute(
            """
            UPDATE skills
            SET version=$2, manifest=$3::jsonb, system_prompt=$4, tools=$5::jsonb,
                updated_at=now()
            WHERE name=$1
            """,
            skill_name,
            target_version,
            json.dumps(manifest),
            system_prompt,
            json.dumps(tools_yaml),
        )
        # 更新 latest_version 指针(新会话加载该版本)
        await self._upsert_config_runtime(
            conn,
            key=f"skill.{skill_name}.latest_version",
            value=target_version,
        )
        return {
            "rolled_back_to": target_version,
            "scope": "skill",
            "affected_sessions": 0,
        }

    def rollback_harness(self, *, target_commit: str) -> dict:
        """Harness 代码回滚:返回 git revert 指令(不自动执行)(AC-5)。

        单人开发手动 git revert + 重新部署。

        Returns:
            {command: f"git revert {target_commit}", note: "手动执行后重启 Sidecar"}
        """
        return {
            "command": f"git revert {target_commit}",
            "note": "手动执行后重启 Sidecar",
        }

    async def _upsert_config_runtime(
        self, conn: asyncpg.Connection, *, key: str, value: str
    ) -> None:
        """upsert config_runtime 表 key=value 指针。"""
        await conn.execute(
            """
            INSERT INTO config_runtime (key, value, updated_at)
            VALUES ($1, $2::jsonb, now())
            ON CONFLICT (key) DO UPDATE SET value=$2::jsonb, updated_at=now()
            """,
            key,
            json.dumps(value),  # 存为 JSON 字符串
        )
