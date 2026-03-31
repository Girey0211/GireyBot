"""
스킬 시스템 — SKILL.md 기반 선언적 에이전트 플레이북

OpenClaw AgentSkills 표준에서 영감을 받은 스킬 시스템입니다.
각 스킬은 YAML frontmatter + Markdown 본문으로 정의됩니다.
"""

from src.main.skills.models import Skill, SkillMatchResult
from src.main.skills.loader import SkillLoader
from src.main.skills.router import SkillRouter
from src.main.skills.executor import SkillExecutor

__all__ = [
    "Skill",
    "SkillMatchResult",
    "SkillLoader",
    "SkillRouter",
    "SkillExecutor",
]
