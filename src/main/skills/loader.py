"""
스킬 로더 — SKILL.md 파서 및 디스커버리

skills/ 디렉토리를 스캔하고 SKILL.md 파일의
YAML frontmatter + Markdown 본문을 파싱합니다.

로딩 우선순위:
1. 서버별 스킬 (최고): config/guilds/{guild_id}/skills/
2. 전역 스킬:          skills/
"""

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from src.main.skills.models import Skill

logger = logging.getLogger("girey-bot.skills.loader")

# 프로젝트 루트
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
GLOBAL_SKILLS_DIR = BASE_DIR / "src" / "shared" / "skills"
GUILDS_DIR = BASE_DIR / "config" / "guilds"
CREDENTIALS_DIR = BASE_DIR / "data" / "credentials"

# YAML frontmatter 구분자 패턴
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


def _parse_skill_md(path: Path) -> Skill | None:
    """
    SKILL.md 파일을 파싱하여 Skill 객체를 반환합니다.

    형식:
        ---
        name: skill-name
        description: "설명"
        triggers: [...]
        ...
        ---
        # 마크다운 본문
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"스킬 파일 읽기 실패: {path} — {e}")
        return None

    match = _FRONTMATTER_RE.match(raw)
    if not match:
        logger.warning(f"frontmatter를 찾을 수 없음: {path}")
        return None

    frontmatter_str, body = match.group(1), match.group(2)

    try:
        fm: dict[str, Any] = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError as e:
        logger.error(f"frontmatter YAML 파싱 실패: {path} — {e}")
        return None

    name = fm.get("name") or path.parent.name
    description = fm.get("description", "")

    if not description:
        logger.warning(f"description이 없는 스킬: {name} ({path})")

    # metadata — 단일 라인 JSON 또는 YAML dict
    metadata = fm.get("metadata", {})
    if isinstance(metadata, str):
        try:
            import json
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}

    return Skill(
        name=name,
        description=description,
        body=body.strip(),
        triggers=fm.get("triggers", []),
        user_invocable=fm.get("user-invocable", True),
        disable_model_invocation=fm.get("disable-model-invocation", False),
        executor=fm.get("executor"),
        credentials=fm.get("credentials"),
        metadata=metadata,
        base_dir=path.parent,
    )


def _check_requirements(skill: Skill, config: dict[str, Any]) -> bool:
    """
    metadata.requires 게이팅 검사.

    충족하지 않는 스킬은 로드하지 않습니다.
    """
    requires = skill.metadata.get("requires", {})
    if not requires:
        return True

    # config 조건: 설정 값이 truthy여야 함
    for config_path in requires.get("config", []):
        keys = config_path.split(".")
        value = config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                value = None
                break
        if not value:
            logger.info(
                f"스킬 '{skill.name}' 비활성화: "
                f"config 조건 미충족 — {config_path}"
            )
            return False

    return True


class SkillLoader:
    """스킬 디스커버리 및 로딩 관리자"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._skills: dict[str, Skill] = {}
        # credentials 누락으로 비활성화된 스킬 목록 (안내용)
        self.unconfigured_skills: dict[str, str] = {}  # name → 필요한 파일명

    @property
    def skills(self) -> dict[str, Skill]:
        return self._skills

    def load_all(self, guild_id: int | str | None = None) -> dict[str, Skill]:
        """
        전역 + 서버별 스킬을 로드합니다.

        서버별 스킬이 전역 스킬과 이름이 같으면 덮어씁니다.
        config의 skills.enabled / skills.disabled 로 필터링합니다.
        """
        self._skills.clear()

        # 1. 전역 스킬 로드
        self._load_from_dir(GLOBAL_SKILLS_DIR)

        # 2. 서버별 스킬 로드 (덮어쓰기)
        if guild_id is not None:
            guild_skills_dir = GUILDS_DIR / str(guild_id) / "skills"
            self._load_from_dir(guild_skills_dir)

        # 3. enabled/disabled 필터
        self._apply_filters()

        # 4. credentials 파일 로드 → config에 병합
        self._load_credentials()

        # 5. 서버별 스킬 config 오버라이드 적용 (credentials 위에 덮어씀)
        self._apply_skill_entries()

        logger.info(
            f"스킬 로드 완료: {len(self._skills)}개 "
            f"[{', '.join(self._skills.keys())}]"
        )
        return self._skills

    def _load_from_dir(self, skills_dir: Path) -> None:
        """디렉토리에서 SKILL.md 파일을 스캔하여 로드합니다."""
        if not skills_dir.is_dir():
            return

        for skill_md in skills_dir.rglob("SKILL.md"):
            skill = _parse_skill_md(skill_md)
            if skill is None:
                continue

            if not _check_requirements(skill, self.config):
                continue

            self._skills[skill.name] = skill
            logger.debug(f"스킬 로드: {skill.name} ({skill_md})")

    def _apply_filters(self) -> None:
        """config의 skills.enabled / skills.disabled로 필터링합니다."""
        skills_config = self.config.get("skills", {})
        enabled = skills_config.get("enabled", [])
        disabled = skills_config.get("disabled", [])

        # enabled가 비어있으면 전체 활성화 (disabled만 적용)
        if enabled:
            self._skills = {
                name: skill
                for name, skill in self._skills.items()
                if name in enabled
            }

        if disabled:
            for name in disabled:
                if name in self._skills:
                    del self._skills[name]
                    logger.debug(f"스킬 비활성화 (disabled): {name}")

    def _load_credentials(self) -> None:
        """
        credentials 필드가 지정된 스킬의 접속 정보를 data/credentials/에서 로드합니다.

        credentials 파일의 내용은 skill.config에 병합됩니다.
        파일이 없으면 경고 로그를 남기고 스킬을 비활성화합니다.
        """
        self.unconfigured_skills.clear()
        to_remove = []
        for name, skill in self._skills.items():
            if not skill.credentials:
                continue

            cred_path = CREDENTIALS_DIR / skill.credentials
            if not cred_path.exists():
                logger.warning(
                    f"스킬 '{name}' 비활성화: "
                    f"credentials 파일 없음 — {cred_path}\n"
                    f"  → {cred_path.with_suffix('')}.example.yaml 을 참고하세요."
                )
                self.unconfigured_skills[name] = skill.credentials
                to_remove.append(name)
                continue

            try:
                cred_data = yaml.safe_load(
                    cred_path.read_text(encoding="utf-8")
                ) or {}
            except Exception as e:
                logger.error(f"credentials 파싱 실패: {cred_path} — {e}")
                to_remove.append(name)
                continue

            # credentials → config 베이스로 설정
            skill.config = cred_data
            logger.debug(f"credentials 로드: {name} ← {cred_path.name}")

        for name in to_remove:
            del self._skills[name]

    def _apply_skill_entries(self) -> None:
        """
        config의 skills.entries에서 스킬별 설정 오버라이드를 적용합니다.

        credentials에서 로드된 config 위에 deep merge됩니다.
        """
        from src.shared.config import deep_merge

        entries = self.config.get("skills", {}).get("entries", {})
        for name, entry_config in entries.items():
            if name in self._skills:
                override = entry_config.get("config", {})
                if override:
                    self._skills[name].config = deep_merge(
                        self._skills[name].config, override
                    )

    def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def get_invocable_skills(self) -> list[Skill]:
        """user-invocable: true인 스킬 목록 반환 (슬래시 명령어용)"""
        return [s for s in self._skills.values() if s.user_invocable]

    def get_auto_skills(self) -> list[Skill]:
        """자동 매칭 가능한 스킬 목록 (disable-model-invocation이 아닌 것)"""
        return [s for s in self._skills.values() if not s.disable_model_invocation]
