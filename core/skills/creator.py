"""
스킬 생성 엔진 — core 구현체

skill-creator 스킬 방식을 대체합니다.
사전 정의된 SKILL_SCHEMA 기반으로 LLM에게 구조화된 JSON 응답을 요청하고,
SKILL.md 파일을 생성합니다.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from core.llm.base import BaseLLMClient
from core.skills.loader import GLOBAL_SKILLS_DIR

logger = logging.getLogger("girey-bot.skills.creator")

# ─── 프롬프트 로드 ────────────────────────────────────────────
_PROMPT_DIR = Path(__file__).parent / "prompts"
_EXTRACT_PROMPT = (_PROMPT_DIR / "creator_extract.txt").read_text(encoding="utf-8")
_BODY_PROMPT_TEMPLATE = (_PROMPT_DIR / "creator_body.txt").read_text(encoding="utf-8")


# ─── 스킬 스키마 정의 ─────────────────────────────────────────

SKILL_SCHEMA: dict[str, dict[str, Any]] = {
    "name": {
        "required": True,
        "desc": "영문 소문자, 하이픈 가능 (예: my-server)",
    },
    "description": {
        "required": True,
        "desc": "스킬이 하는 일 한 줄 요약",
    },
    "triggers": {
        "required": True,
        "desc": "활성화 키워드 목록 (한국어/영어 모두 포함, 최소 3개)",
    },
    "executor": {
        "required": False,
        "desc": "ssh / local / docker / 없으면 생략",
    },
    "credentials": {
        "required": False,
        "desc": "접속 정보 파일명 (executor가 ssh일 때, 예: my-server.yaml)",
    },
    "notes": {
        "required": False,
        "desc": "추가 정보, 주의사항, 참고 내용 등 자유 형식 비고",
    },
}


@dataclass
class SkillDraft:
    """스킬 생성을 위해 수집된 정보"""

    name: str
    description: str
    triggers: list[str]
    executor: Optional[str] = None
    credentials: Optional[str] = None
    notes: Optional[str] = None

    # 생성된 SKILL.md 본문 (generate_body 이후 채워짐)
    body: str = field(default="", repr=False)

    def to_frontmatter_dict(self) -> dict[str, Any]:
        """SKILL.md frontmatter용 딕셔너리 반환"""
        fm: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.triggers:
            fm["triggers"] = self.triggers
        fm["user-invocable"] = True
        if self.executor:
            fm["executor"] = self.executor
        if self.credentials:
            fm["credentials"] = self.credentials
        return fm


class SkillCreationError(Exception):
    """스킬 생성 중 발생하는 오류"""


class SkillCreator:
    """
    사전 정의된 SKILL_SCHEMA 기반으로 스킬을 생성합니다.

    흐름:
    1. collect_info(): LLM에게 JSON 형식으로 스킬 정보 추출 요청
    2. generate_body(): LLM에게 SKILL.md 본문 생성 요청
    3. write_skill(): SKILL.md 파일 작성
    """

    def __init__(self, llm_client: BaseLLMClient):
        self.llm_client = llm_client

    # ─── 1단계: 스킬 정보 추출 ──────────────────────────────

    async def collect_info(self, user_message: str) -> SkillDraft:
        """
        사용자 메시지에서 스킬 정보를 추출합니다.

        LLM이 SKILL_SCHEMA 기반 JSON을 반환하면 SkillDraft로 변환합니다.
        LLM 없이 키워드 기반 최소 draft를 생성하는 폴백도 포함합니다.

        Raises:
            SkillCreationError: LLM 응답 실패 또는 필수 필드 누락
        """
        response = await self.llm_client.chat(
            prompt=user_message,
            system_prompt=_EXTRACT_PROMPT,
        )

        if not response.available or not response.content:
            raise SkillCreationError(
                f"LLM 응답 실패: {response.reason or '알 수 없는 오류'}"
            )

        raw = response.content.strip()
        data = self._parse_json(raw)

        # 필수 필드 검증
        for field_name, meta in SKILL_SCHEMA.items():
            if meta["required"] and not data.get(field_name):
                raise SkillCreationError(
                    f"필수 필드 누락: '{field_name}' — {meta['desc']}"
                )

        # name 검증 (영문 소문자 + 하이픈)
        name = str(data.get("name", "")).strip().lower().replace(" ", "-")
        if not name or not all(c.isalnum() or c == "-" for c in name):
            raise SkillCreationError(
                "스킬 이름은 영문 소문자와 하이픈(-)만 사용할 수 있습니다."
            )

        # credentials 확장자 보정
        creds = data.get("credentials") or None
        if creds and not creds.endswith(".yaml"):
            creds += ".yaml"

        return SkillDraft(
            name=name,
            description=str(data.get("description", "")).strip(),
            triggers=[
                t.strip() for t in (data.get("triggers") or []) if t.strip()
            ],
            executor=data.get("executor") or None,
            credentials=creds,
            notes=data.get("notes") or None,
        )

    # ─── 2단계: 본문 생성 ────────────────────────────────────

    async def generate_body(self, draft: SkillDraft) -> str:
        """
        SkillDraft 정보를 바탕으로 SKILL.md 본문(마크다운)을 생성합니다.

        notes가 있으면 본문 최하단 ## 비고 섹션에 포함됩니다.
        """
        triggers_str = ", ".join(draft.triggers)
        executor_str = draft.executor or "없음 (순수 대화 스킬)"
        notes_str = draft.notes or "없음"

        system_prompt = _BODY_PROMPT_TEMPLATE.format(
            name=draft.name,
            description=draft.description,
            triggers=triggers_str,
            executor=executor_str,
            notes=notes_str,
        )

        response = await self.llm_client.chat(
            prompt=(
                f"스킬 이름: {draft.name}\n"
                f"설명: {draft.description}\n"
                f"트리거: {triggers_str}\n"
                f"실행기: {executor_str}\n"
                f"비고: {notes_str}\n\n"
                "위 정보를 바탕으로 SKILL.md 본문을 작성해주세요."
            ),
            system_prompt=system_prompt,
        )

        if not response.available or not response.content:
            # 폴백: 기본 템플릿 반환
            logger.warning(
                f"본문 생성 LLM 실패, 기본 템플릿 사용: {draft.name}"
            )
            return self._default_body(draft)

        body = response.content.strip()

        # LLM이 frontmatter를 포함시킨 경우 제거
        body = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", body, flags=re.DOTALL).strip()

        return body

    # ─── 3단계: 파일 작성 ────────────────────────────────────

    def write_skill(self, draft: SkillDraft, body: str) -> Path:
        """
        SKILL.md 파일을 생성합니다.

        Returns:
            생성된 SKILL.md 경로

        Raises:
            SkillCreationError: 디렉토리가 이미 존재하거나 파일 쓰기 실패
        """
        skill_dir = GLOBAL_SKILLS_DIR / draft.name
        if skill_dir.exists():
            raise SkillCreationError(
                f"스킬 `{draft.name}` 디렉토리가 이미 존재합니다."
            )

        # Body에 notes 섹션 추가 (LLM이 빠뜨렸을 경우 보완)
        if draft.notes and "## 비고" not in body:
            body = body.rstrip() + f"\n\n## 비고\n\n{draft.notes}\n"

        # frontmatter 구성
        fm = draft.to_frontmatter_dict()
        fm_yaml = yaml.dump(
            fm, allow_unicode=True, default_flow_style=False
        ).strip()

        content = f"---\n{fm_yaml}\n---\n\n{body.strip()}\n"

        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path = skill_dir / "SKILL.md"
            skill_path.write_text(content, encoding="utf-8")
            logger.info(f"스킬 생성 완료: {draft.name} → {skill_path}")
            return skill_path
        except OSError as e:
            raise SkillCreationError(f"파일 작성 실패: {e}") from e

    # ─── 유틸 ────────────────────────────────────────────────

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """LLM 응답에서 JSON 객체를 추출합니다."""
        # ```json ... ``` 래핑 제거
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip())
        raw = re.sub(r"\n?```\s*$", "", raw.strip())

        # 중괄호 추출
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise SkillCreationError(
                "LLM 응답에서 JSON을 찾을 수 없습니다."
            )
        try:
            return json.loads(match.group())
        except json.JSONDecodeError as e:
            raise SkillCreationError(f"JSON 파싱 실패: {e}") from e

    @staticmethod
    def _default_body(draft: SkillDraft) -> str:
        """LLM 본문 생성 실패 시 사용하는 기본 템플릿"""
        lines = [
            f"# {draft.name}",
            "",
            "## 트리거 조건",
            "",
            f"사용자가 관련 키워드를 포함한 메시지를 보낼 때 활성화됩니다.",
            f"`/skill {draft.name}` 슬래시 명령어로 직접 실행할 수 있습니다.",
            "",
            "## 의도 분류",
            "",
            "| 의도 | 키워드 예시 |",
            "| --- | --- |",
            "| `status` | 상태, 확인, status |",
            "",
            "## 실행 절차",
            "",
            "1. (실행 절차를 작성하세요)",
            "",
            "## 출력 형식",
            "",
            "Embed 메시지로 응답한다.",
            "",
            "## 에러 처리",
            "",
            '- 오류 발생 → "처리 중 오류가 발생했습니다."',
        ]
        if draft.notes:
            lines += ["", "## 비고", "", draft.notes]
        return "\n".join(lines) + "\n"
