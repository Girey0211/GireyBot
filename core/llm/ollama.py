"""
Ollama 기반 LLM 클라이언트

Ollama 네이티브 API(/api/chat)를 사용하여 요청을 전송합니다.
think=False 를 페이로드에 직접 포함하여 Qwen3 계열의 think 모드를 비활성화합니다.
"""

import logging

import aiohttp

from core.llm.base import BaseLLMClient, LLMResponse

logger = logging.getLogger("girey-bot.llm")

DEFAULT_HOST = "http://localhost:11434"


class OllamaClient(BaseLLMClient):
    """Ollama 네이티브 API 기반 LLM 클라이언트"""

    def __init__(self, model: str = "llama3", host: str | None = None):
        super().__init__(model)
        self.host = host or DEFAULT_HOST
        self._api_url = f"{self.host}/api/chat"
        self._available = True
        logger.info(f"OllamaClient 초기화 완료 — model={self.model}, host={self.host}")

    async def _request(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 2000) -> str:
        """Ollama /api/chat 엔드포인트에 요청을 보내고 content를 반환합니다."""
        payload = {
            "model": self.model,
            "messages": messages,
            "think": False,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self._api_url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["message"]["content"]

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
        previous_user_message: str,
        bot_response: str,
    ) -> LLMResponse:
        """후속 메시지가 이전 대화의 연속인지 Ollama로 판단합니다."""
        if not self._available:
            return self._unavailable_response("Ollama 서버에 연결할 수 없습니다.")

        system_prompt = (
            "당신은 Discord 대화 흐름 분석기입니다.\n"
            "이전 대화(사용자 메시지 + 봇 응답)와 새 메시지를 비교하여, "
            "새 메시지가 이전 대화의 자연스러운 연속인지 판단하세요.\n\n"
            "다음 경우 연속 대화로 판단하세요:\n"
            "- 이전 대화 주제에 대한 추가 질문이나 코멘트\n"
            "- 봇의 응답에 대한 후속 요청이나 피드백\n"
            "- 같은 맥락의 관련 질문\n\n"
            "다음 경우 연속 대화가 아닌 것으로 판단하세요:\n"
            "- 완전히 다른 주제의 대화\n"
            "- 다른 사람을 부르거나 다른 사람에게 말하는 뉘앙스\n"
            "- 봇과 무관한 일상 대화 (인사, 잡담 등)\n"
            "- 단순 감탄사나 리액션 (ㅋㅋ, ㅎㅎ, ㄳ, ㅇㅋ 등 단독 사용)\n\n"
            "반드시 JSON으로만 응답하세요:\n"
            '{"is_continuation": true/false, "reason": "판단 근거"}'
        )

        user_prompt = (
            f"[이전 사용자 메시지]\n{previous_user_message}\n\n"
            f"[봇 응답]\n{bot_response}\n\n"
            f"[새 메시지]\n{new_message}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            content = await self._request(messages, temperature=0.1, max_tokens=200)
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

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": prompt})

        try:
            content = await self._request(messages, temperature=0.7, max_tokens=2000)
            return LLMResponse(available=True, content=content)
        except Exception as e:
            logger.error(f"[Ollama] 채팅 요청 실패: {e}")
            return self._unavailable_response(f"Ollama API 호출 실패: {e}")
