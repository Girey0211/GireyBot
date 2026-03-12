"""
LLM 클라이언트 — 다중 프로바이더 지원

팩토리 함수 create_llm_client()로 config에 따라 적절한 클라이언트를 생성합니다.
"""

import os
import logging
from typing import Any

from core.llm.base import BaseLLMClient, LLMClient, LLMResponse
from core.llm.ollama import OllamaClient
from core.llm.openai import OpenAIClient

logger = logging.getLogger("girey-bot.llm")

__all__ = [
    "BaseLLMClient",
    "LLMClient",
    "LLMResponse",
    "OllamaClient",
    "OpenAIClient",
    "create_llm_client",
]


def create_llm_client(config: dict[str, Any]) -> BaseLLMClient:
    """
    설정에 따라 적절한 LLM 클라이언트를 생성합니다.

    config 구조:
        llm:
          provider: "ollama"
          ollama:
            model: "llama3"
            host: null
          openai:
            model: "gpt-4o-mini"
            api_key: null
            base_url: null
    """
    llm_config = config.get("llm", {})
    provider = llm_config.get("provider", "ollama").lower()
    provider_config = llm_config.get(provider, {})

    logger.info(f"LLM 프로바이더 선택: {provider}")

    if provider == "openai":
        return OpenAIClient(
            model=provider_config.get("model", "gpt-4o-mini"),
            api_key=provider_config.get("api_key"),
            base_url=provider_config.get("base_url"),
        )
    else:
        return OllamaClient(
            model=provider_config.get("model", "llama3"),
            host=provider_config.get("host") or os.getenv("OLLAMA_HOST"),
        )
