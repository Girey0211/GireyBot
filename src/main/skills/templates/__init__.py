"""스킬 템플릿 모듈"""

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent
_SKILL_BODY_TEMPLATE: str | None = None


def load_skill_body_template() -> str:
    """skill_body.md 템플릿을 로드하여 캐싱합니다."""
    global _SKILL_BODY_TEMPLATE
    if _SKILL_BODY_TEMPLATE is None:
        _SKILL_BODY_TEMPLATE = (
            (_TEMPLATE_DIR / "skill_body.md").read_text(encoding="utf-8")
        )
    return _SKILL_BODY_TEMPLATE


def render_skill_body(name: str, sections: dict[str, str]) -> str:
    """템플릿에 sections 값을 채워 SKILL.md 본문을 생성합니다.

    sections 키: trigger_conditions, intent_table,
                 execution_steps, output_format, error_handling
    """
    template = load_skill_body_template()
    defaults = {
        "name": name,
        "trigger_conditions": (
            "사용자가 관련 키워드를 포함한 메시지를 보낼 때 활성화됩니다.\n"
            f"`/skill {name}` 슬래시 명령어로 직접 실행할 수 있습니다."
        ),
        "intent_table": (
            "| 의도 | 키워드 예시 |\n"
            "| --- | --- |\n"
            "| `default` | (기본) |"
        ),
        "execution_steps": "1. (여기에 실행 절차를 작성하세요)",
        "output_format": "Embed 메시지로 응답한다.",
        "error_handling": (
            "- 연결 실패 → \"서버에 접근할 수 없습니다.\"\n"
            "- 권한 부족 → \"실행 권한이 없습니다.\""
        ),
    }
    defaults.update({k: v.strip() for k, v in sections.items() if v})
    return template.format(**defaults)
