"""
스킬 데이터 모델
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    """SKILL.md에서 파싱된 스킬 정의"""

    name: str
    description: str
    body: str                                   # 마크다운 본문 (LLM 지시문)
    triggers: list[str] = field(default_factory=list)
    user_invocable: bool = True
    disable_model_invocation: bool = False
    executor: str | None = None                 # "ssh" | "local" | "docker" | None
    credentials: str | None = None              # data/credentials/ 내 파일명
    metadata: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)   # 서버별 오버라이드 + credentials 병합
    base_dir: Path = field(default_factory=Path)


@dataclass
class SkillMatchResult:
    """스킬 라우팅 결과 — 신뢰도 점수 포함"""

    skill: Skill | None = None
    confidence: float = 0.0         # 0~100 스케일
    trigger_type: str | None = None # "trigger" | "llm" | "direct"
    matched_trigger: str | None = None
    candidates: list["SkillMatchCandidate"] = field(default_factory=list)
    needs_clarification: bool = False
    is_management_request: bool = False  # 관리 의도 감지 시 True (이중 안전장치)


@dataclass
class SkillMatchCandidate:
    """라우팅 후보 스킬 — 재확인 UI에 사용"""

    skill: Skill
    confidence: float
    matched_trigger: str | None = None
    reason: str = ""
