"""
OpenAI API 기반 LLM 클라이언트
"""

import os
import logging

from src.shared.llm.base import BaseLLMClient, LLMResponse

logger = logging.getLogger("girey-bot.llm")


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

    async def analyze_continuation(
        self,
        new_message: str,
        bot_response: str,
    ) -> LLMResponse:
        """후속 메시지가 이전 대화의 연속인지 OpenAI로 판단합니다."""
        if not self._available or not self._client:
            return self._unavailable_response(
                "OpenAI API가 설정되지 않았습니다."
            )

        system_prompt = (
            "봇 응답에 이어 새 메시지가 왔을 때 연속 대화인지 판단하세요.\n"
            "연속: 봇 응답에 대한 후속 질문·피드백·관련 요청\n"
            "비연속: 전혀 다른 주제, 단순 감탄사(ㅋㅋ·ㄳ·ㅇㅋ), 다른 사람과의 대화\n"
            'JSON만 출력: {"is_continuation": true/false}'
        )

        user_prompt = (
            f"[봇 응답]\n{bot_response}\n\n"
            f"[새 메시지]\n{new_message}"
        )

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
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
            logger.error(f"[OpenAI] 대화 연속성 분석 실패: {e}")
            return self._unavailable_response(f"OpenAI API 호출 실패: {e}")

    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> LLMResponse:
        """OpenAI로 채팅 요청을 처리합니다."""
        if not self._available or not self._client:
            return self._unavailable_response(
                "OpenAI API가 설정되지 않았습니다."
            )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if context:
            messages.append({"role": "system", "content": context})
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
