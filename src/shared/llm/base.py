"""
LLM 클라이언트 — 베이스 클래스 및 공통 응답 모델
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    """LLM 응답 결과"""

    available: bool
    content: str
    reason: str | None = None
    raw: dict[str, Any] | None = None


class BaseLLMClient(ABC):
    """LLM 클라이언트 추상 베이스 클래스"""

    def __init__(self, model: str):
        self.model = model
        self._available = False

    @property
    def provider_name(self) -> str:
        """프로바이더 이름"""
        return self.__class__.__name__

    @property
    def is_available(self) -> bool:
        """LLM 서비스 사용 가능 여부"""
        return self._available

    @abstractmethod
    async def analyze_call_intent(
        self,
        message_content: str,
        context: list[str] | None = None,
    ) -> LLMResponse:
        """메시지의 호출 의도를 분석합니다."""
        ...

    @abstractmethod
    async def analyze_continuation(
        self,
        new_message: str,
        bot_response: str,
    ) -> LLMResponse:
        """후속 메시지가 이전 대화의 연속인지 판단합니다."""
        ...

    @abstractmethod
    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> LLMResponse:
        """일반 채팅 요청을 처리합니다.

        Args:
            prompt: 사용자 메시지
            system_prompt: 시스템 프롬프트
            context: 메모리 컨텍스트 (최근 대화, 유저 팩트 등)
        """
        ...

    def _unavailable_response(self, reason: str) -> LLMResponse:
        """사용 불가 응답 생성 헬퍼"""
        return LLMResponse(available=False, content="", reason=reason)


# 하위 호환: LLMClient 별칭
LLMClient = BaseLLMClient
