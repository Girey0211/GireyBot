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
    "obscene": 30,       # 음란·성적 내용
    "political": 20,     # 정치적 편향 유도
    "unreasonable": 15,  # 무리한·악의적 요청
}

_SYSTEM_PROMPT = """당신은 Discord 메시지의 부적절한 내용을 감지하는 검열 시스템입니다.
다음 세 가지 위반 유형을 판별하세요:

1. **obscene** — 음란하거나 성적인 내용, 성희롱, 포르노 관련 내용
2. **political** — 특정 정치인/정당 지지·비하, 정치적 편향 유도, 선동
3. **unreasonable** — 봇이 불법 행위를 돕도록 요청, 개인정보 침해 유도, 악의적이거나 명백히 무리한 요청

다음 JSON만 출력하세요 (다른 텍스트 없이):
{
  "violation": true 또는 false,
  "type": "obscene" | "political" | "unreasonable" | "none",
  "reason": "한 줄 이유 (영어 또는 한국어)"
}

일반적인 대화, 서버 관리 질문, 기술 질문은 위반이 아닙니다. 판단이 애매하면 false로 처리하세요."""


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
        reason = data.get("reason", "")
        score_delta = VIOLATION_SCORES.get(vtype, 0) if violation else 0

        if violation:
            logger.info(f"콘텐츠 위반 감지 — type={vtype} score={score_delta} reason={reason}")

        return ContentCheckResult(
            violation=violation,
            violation_type=vtype,
            score_delta=score_delta,
            reason=reason,
        )

    except Exception as e:
        logger.warning(f"콘텐츠 검열 실패: {e}")
        return ContentCheckResult(violation=False, violation_type="none", score_delta=0, reason=str(e))
