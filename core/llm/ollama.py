"""
Ollama 기반 LLM 클라이언트

현재는 스텁 구현. Ollama 셋업 완료 후 실제 API 호출로 교체 예정.
"""

import logging

from core.llm.base import BaseLLMClient, LLMResponse

logger = logging.getLogger("girey-bot.llm")


class OllamaClient(BaseLLMClient):
    """Ollama 기반 LLM 클라이언트 (스텁)"""

    def __init__(self, model: str = "llama3", host: str | None = None):
        super().__init__(model)
        self.host = host
        self._available = False

        logger.info(
            f"OllamaClient 초기화 (스텁) — model={model}, host={host}"
        )

    async def analyze_call_intent(
        self,
        message_content: str,
        context: list[str] | None = None,
    ) -> LLMResponse:
        logger.debug(f"[Ollama] 호출 의도 분석 (스텁): {message_content[:50]}...")
        return self._unavailable_response(
            "Ollama 서버가 아직 설정되지 않았습니다. "
            "Ollama 설치 및 모델 다운로드 후 사용 가능합니다."
        )

    async def analyze_continuation(
        self,
        new_message: str,
        previous_user_message: str,
        bot_response: str,
    ) -> LLMResponse:
        logger.debug(f"[Ollama] 대화 연속성 분석 (스텁): {new_message[:50]}...")
        return self._unavailable_response(
            "Ollama 서버가 아직 설정되지 않았습니다."
        )

    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> LLMResponse:
        logger.debug(f"[Ollama] 채팅 요청 (스텁): {prompt[:50]}...")
        return self._unavailable_response(
            "Ollama 서버가 아직 설정되지 않았습니다. "
            "Ollama 설치 및 모델 다운로드 후 사용 가능합니다."
        )
