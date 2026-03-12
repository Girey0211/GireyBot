"""
호출 감지 데이터 모델
"""

from dataclasses import dataclass

from core.llm.base import LLMResponse

# 대화 연속성 타임아웃 (초) — 봇 응답 후 이 시간 내의 메시지만 연속 대화로 간주
CONTINUATION_TIMEOUT_SECONDS = 300  # 5분


@dataclass
class ActiveConversation:
    """채널별 활성 대화 상태"""

    channel_id: int
    last_bot_response: str          # 봇의 마지막 응답 내용
    last_user_message: str          # 사용자의 마지막 메시지 내용
    timestamp: float                # 마지막 응답 시각 (time.time())


@dataclass
class CallDetectionResult:
    """호출 감지 결과"""

    detected: bool = False
    trigger_type: str | None = None  # "mention" | "keyword" | "auto_detect" | "continuation"
    matched_keyword: str | None = None
    confidence: float = 0.0
    llm_response: LLMResponse | None = None
