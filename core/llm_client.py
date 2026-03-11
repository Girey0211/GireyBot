"""
LLM 클라이언트 — 다중 프로바이더 지원

프로바이더별 구현:
- OllamaClient: 로컬/원격 Ollama 서버 (스텁)
- OpenAIClient: OpenAI API 키 방식

팩토리 함수 create_llm_client()로 config에 따라 적절한 클라이언트를 생성합니다.
"""

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("girey-bot.llm")


# ─── 공통 응답 ────────────────────────────────────────────────
@dataclass
class LLMResponse:
    """LLM 응답 결과"""

    available: bool
    content: str
    reason: str | None = None
    raw: dict[str, Any] | None = None


# ─── 베이스 클래스 ────────────────────────────────────────────
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
    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """일반 채팅 요청을 처리합니다."""
        ...

    def _unavailable_response(self, reason: str) -> LLMResponse:
        """사용 불가 응답 생성 헬퍼"""
        return LLMResponse(available=False, content="", reason=reason)


# ─── Ollama 프로바이더 ────────────────────────────────────────
class OllamaClient(BaseLLMClient):
    """
    Ollama 기반 LLM 클라이언트.

    현재는 스텁 구현. Ollama 셋업 완료 후 실제 API 호출로 교체 예정.
    """

    def __init__(self, model: str = "llama3", host: str | None = None):
        super().__init__(model)
        self.host = host
        self._available = False  # Ollama 셋업 전까지 False

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

    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        logger.debug(f"[Ollama] 채팅 요청 (스텁): {prompt[:50]}...")
        return self._unavailable_response(
            "Ollama 서버가 아직 설정되지 않았습니다. "
            "Ollama 설치 및 모델 다운로드 후 사용 가능합니다."
        )


# ─── OpenAI 프로바이더 ────────────────────────────────────────
class OpenAIClient(BaseLLMClient):
    """
    OpenAI API 기반 LLM 클라이언트.

    OPENAI_API_KEY 환경변수 또는 config에서 api_key를 받아 사용합니다.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        super().__init__(model)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url
        self._client = None

        if not self.api_key:
            logger.warning(
                "OpenAIClient: OPENAI_API_KEY가 설정되지 않았습니다. "
                "LLM 기능이 비활성화됩니다."
            )
            self._available = False
        else:
            self._init_client()

    def _init_client(self):
        """OpenAI 클라이언트를 초기화합니다."""
        try:
            from openai import AsyncOpenAI

            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url

            self._client = AsyncOpenAI(**kwargs)
            self._available = True
            logger.info(
                f"OpenAIClient 초기화 완료 — model={self.model}"
            )
        except ImportError:
            logger.error(
                "openai 패키지가 설치되지 않았습니다. "
                "'uv add openai' 로 설치하세요."
            )
            self._available = False
        except Exception as e:
            logger.error(f"OpenAI 클라이언트 초기화 실패: {e}")
            self._available = False

    async def analyze_call_intent(
        self,
        message_content: str,
        context: list[str] | None = None,
    ) -> LLMResponse:
        """OpenAI로 메시지의 호출 의도를 분석합니다."""
        if not self._available or not self._client:
            return self._unavailable_response(
                "OpenAI API가 설정되지 않았습니다."
            )

        system_prompt = (
            "당신은 Discord 서버 지원 봇의 호출 판단기입니다.\n"
            "사용자의 메시지를 분석하여, 봇에게 도움을 요청하는 것인지 판단하세요.\n"
            "반드시 JSON으로 응답하세요: "
            '{"should_respond": true/false, "confidence": 0.0~1.0, "reason": "판단 근거"}'
        )

        context_text = ""
        if context:
            context_text = "\n\n[이전 대화]\n" + "\n".join(context[-5:])

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message_content + context_text},
                ],
                temperature=0.1,
                max_tokens=200,
            )

            content = response.choices[0].message.content or ""

            return LLMResponse(
                available=True,
                content=content,
                raw={"usage": response.usage.model_dump() if response.usage else None},
            )

        except Exception as e:
            logger.error(f"[OpenAI] 호출 의도 분석 실패: {e}")
            return self._unavailable_response(f"OpenAI API 호출 실패: {e}")

    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """OpenAI로 채팅 요청을 처리합니다."""
        if not self._available or not self._client:
            return self._unavailable_response(
                "OpenAI API가 설정되지 않았습니다."
            )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000,
            )

            content = response.choices[0].message.content or ""

            return LLMResponse(
                available=True,
                content=content,
                raw={"usage": response.usage.model_dump() if response.usage else None},
            )

        except Exception as e:
            logger.error(f"[OpenAI] 채팅 요청 실패: {e}")
            return self._unavailable_response(f"OpenAI API 호출 실패: {e}")


# ─── 팩토리 함수 ──────────────────────────────────────────────

# 하위 호환: LLMClient 별칭
LLMClient = BaseLLMClient


def create_llm_client(config: dict[str, Any]) -> BaseLLMClient:
    """
    설정에 따라 적절한 LLM 클라이언트를 생성합니다.

    Spring Boot 스타일로 provider를 선택하고,
    각 프로바이더별 하위 설정에서 config를 읽습니다.

    config 구조:
        llm:
          provider: "ollama"          # 활성 프로바이더 선택

          ollama:                     # Ollama 전용 설정
            model: "llama3"
            host: null

          openai:                     # OpenAI 전용 설정
            model: "gpt-4o-mini"
            api_key: null
            base_url: null
    """
    llm_config = config.get("llm", {})
    provider = llm_config.get("provider", "ollama").lower()

    # 선택된 프로바이더의 하위 설정을 가져옴
    provider_config = llm_config.get(provider, {})

    logger.info(f"LLM 프로바이더 선택: {provider}")

    if provider == "openai":
        return OpenAIClient(
            model=provider_config.get("model", "gpt-4o-mini"),
            api_key=provider_config.get("api_key"),
            base_url=provider_config.get("base_url"),
        )
    else:
        # 기본값: Ollama
        return OllamaClient(
            model=provider_config.get("model", "llama3"),
            host=provider_config.get("host") or os.getenv("OLLAMA_HOST"),
        )

