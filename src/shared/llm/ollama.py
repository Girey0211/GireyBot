"""
Ollama 기반 LLM 클라이언트

ollama Python 패키지(AsyncClient)를 사용하여 요청을 전송합니다.
"""

import logging

from src.shared.llm.base import BaseLLMClient, LLMResponse

logger = logging.getLogger("girey-bot.llm")

DEFAULT_HOST = "http://localhost:11434"


class OllamaClient(BaseLLMClient):
    """ollama Python 패키지 기반 LLM 클라이언트"""

    def __init__(self, model: str = "llama3", host: str | None = None):
        super().__init__(model)
        self.host = host or DEFAULT_HOST
        self._available = True
        logger.info(f"OllamaClient 초기화 완료 — model={self.model}, host={self.host}")

    def _make_client(self):
        from ollama import AsyncClient
        return AsyncClient(host=self.host)

    async def _request(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 2000) -> str:
        """Ollama에 단일 요청을 보내고 content를 반환합니다."""
        client = self._make_client()
        logger.debug(
            "[Ollama] 요청 전송 — model=%s, messages=%s",
            self.model,
            messages,
        )
        response = await client.chat(
            model=self.model,
            messages=messages,
            stream=False,
            options={
                "temperature": temperature,
                "repeat_penalty": 1.3,
                "num_predict": max_tokens,
                #"stop": ["<end_of_turn>", "<start_of_turn>"],
            },
        )
        return response.message.content

    async def chat_stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ):
        """Ollama 스트리밍 채팅 — 토큰 단위로 content를 yield합니다."""
        if not self._available:
            return

        # system 메시지는 하나로 합쳐야 Gemma 계열 템플릿이 깨지지 않음
        combined_system = "\n\n".join(filter(None, [system_prompt, context]))
        messages = []
        if combined_system:
            messages.append({"role": "system", "content": combined_system})
        messages.append({"role": "user", "content": prompt})

        try:
            client = self._make_client()
            async for chunk in await client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options={
                    "temperature": 0.7,
                    "top_p": 0.95,
                    "top_k": 64,
                    "repeat_penalty": 1.3,
                    "num_predict": 2000,
                    "num_ctx": 2048,
                    "stop": ["<end_of_turn>", "<start_of_turn>"],
                },
            ):
                content = chunk.message.content
                if content:
                    yield content
        except Exception as e:
            logger.error(f"[Ollama] 스트리밍 채팅 실패: {e}")

    async def analyze_call_intent(
        self,
        message_content: str,
        context: list[str] | None = None,
    ) -> LLMResponse:
        """Ollama로 메시지의 호출 의도를 분석합니다."""
        if not self._available:
            return self._unavailable_response("Ollama 서버에 연결할 수 없습니다.")

        system_prompt = (
            "당신은 Discord 서버 지원 봇의 호출 판단기입니다.\n"
            "사용자의 메시지를 분석하여, 봇에게 도움을 요청하는 것인지 판단하세요.\n"
            "반드시 JSON으로 응답하세요: "
            '{"should_respond": true/false, "confidence": 0.0~1.0, "reason": "판단 근거"}'
        )

        context_text = ""
        if context:
            context_text = "\n\n[이전 대화]\n" + "\n".join(context[-5:])

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message_content + context_text},
        ]

        try:
            content = await self._request(messages, temperature=0.1, max_tokens=200)
            return LLMResponse(available=True, content=content)
        except Exception as e:
            logger.error(f"[Ollama] 호출 의도 분석 실패: {e}")
            return self._unavailable_response(f"Ollama API 호출 실패: {e}")

    async def analyze_continuation(
        self,
        new_message: str,
        bot_response: str,
    ) -> LLMResponse:
        """후속 메시지가 이전 대화의 연속인지 Ollama로 판단합니다."""
        if not self._available:
            return self._unavailable_response("Ollama 서버에 연결할 수 없습니다.")

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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            content = await self._request(messages, temperature=0.1, max_tokens=20)
            return LLMResponse(available=True, content=content)
        except Exception as e:
            logger.error(f"[Ollama] 대화 연속성 분석 실패: {e}")
            return self._unavailable_response(f"Ollama API 호출 실패: {e}")

    async def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> LLMResponse:
        """Ollama로 채팅 요청을 처리합니다."""
        if not self._available:
            return self._unavailable_response("Ollama 서버에 연결할 수 없습니다.")

        combined_system = "\n\n".join(filter(None, [system_prompt, context]))
        messages = []
        if combined_system:
            messages.append({"role": "system", "content": combined_system})
        messages.append({"role": "user", "content": prompt})

        try:
            content = await self._request(messages, temperature=0.7, max_tokens=2000)
            return LLMResponse(available=True, content=content)
        except Exception as e:
            logger.error(f"[Ollama] 채팅 요청 실패: {e}")
            return self._unavailable_response(f"Ollama API 호출 실패: {e}")
