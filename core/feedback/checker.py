"""
콘텐츠 위반 감지기

LLM을 사용하여 메시지가 부적절한 내용(음란, 정치, 무리한 요청)인지 분석합니다.
"""

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("girey-bot.feedback")

# 위반 유형별 기본 점수
VIOLATION_SCORES: dict[str, int] = {
    "obscene": 15,       # 음란·성적 내용
    "political": 10,     # 정치적 편향 유도
    "unreasonable": 8,  # 무리한·악의적 요청
}

_SYSTEM_PROMPT = (
    "Discord 메시지의 위반 여부를 판별하세요.\n"
    "위반 유형: obscene(음란·성적), political(정치편향·선동), unreasonable(불법·악의적 요청)\n"
    "일반 대화·기술 질문은 위반 아님. 애매하면 false.\n"
    'JSON만 출력: {"violation": true/false, "type": "obscene|political|unreasonable|none"}'
)


@dataclass
class ContentCheckResult:
    violation: bool
    violation_type: str   # "obscene" | "political" | "unreasonable" | "none"
    score_delta: int      # DB에 추가할 점수
    reason: str


async def check_content(llm_client, message: str) -> ContentCheckResult:
    """메시지 내용을 LLM으로 검사하여 위반 여부를 반환합니다."""
    if not llm_client or not llm_client.is_available:
        return ContentCheckResult(violation=False, violation_type="none", score_delta=0, reason="LLM unavailable")

    try:
        response = await llm_client.chat(
            prompt=f"다음 메시지를 검열하세요:\n\n{message}",
            system_prompt=_SYSTEM_PROMPT,
        )
        if not response.available or not response.content:
            return ContentCheckResult(violation=False, violation_type="none", score_delta=0, reason="no response")

        raw = response.content.strip()
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return ContentCheckResult(violation=False, violation_type="none", score_delta=0, reason="parse error")

        data = json.loads(m.group())
        violation = bool(data.get("violation", False))
        vtype = data.get("type", "none") if violation else "none"
        score_delta = VIOLATION_SCORES.get(vtype, 0) if violation else 0

        if violation:
            logger.info(f"콘텐츠 위반 감지 — type={vtype} score={score_delta}")

        return ContentCheckResult(
            violation=violation,
            violation_type=vtype,
            score_delta=score_delta,
            reason=vtype,
        )

    except Exception as e:
        logger.warning(f"콘텐츠 검열 실패: {e}")
        return ContentCheckResult(violation=False, violation_type="none", score_delta=0, reason=str(e))
