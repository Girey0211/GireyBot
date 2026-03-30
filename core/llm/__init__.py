"""
LLM 클라이언트 — 다중 프로바이더 지원

팩토리 함수 create_llm_clients()로 용도별 클라이언트를 생성합니다.
  - simple   : 단순요청 (호출감지, 콘텐츠검사, 스킬 라우팅, 팩트 추출)
  - roleplay : 롤플레잉 (디스코드 응답, 페르소나 적용)
  - analysis : 분석요청 (스킬 실행, 스킬 생성, 메모리 정리 요약)
"""

import os
import logging
from dataclasses import dataclass
from typing import Any

from core.llm.base import BaseLLMClient, LLMClient, LLMResponse
from core.llm.ollama import OllamaClient
from core.llm.openai import OpenAIClient

logger = logging.getLogger("girey-bot.llm")

__all__ = [
    "BaseLLMClient",
    "LLMClient",
    "LLMClients",
    "LLMResponse",
    "OllamaClient",
    "OpenAIClient",
    "create_llm_clients",
]


@dataclass
class LLMClients:
    """용도별 LLM 클라이언트 묶음"""

    simple: BaseLLMClient    # 단순요청: 호출감지, 콘텐츠검사, 라우팅, 팩트 추출
    roleplay: BaseLLMClient  # 롤플레잉: 디스코드 응답, 페르소나 적용
    analysis: BaseLLMClient  # 분석요청: 스킬 실행, 스킬 생성, 메모리 정리


def create_llm_clients(config: dict[str, Any]) -> LLMClients:
    """
    용도별 LLM 클라이언트를 생성합니다.

    config 구조:
        llm:
          profiles:
            simple:
              provider: openai
              model: gpt-4o-mini
            roleplay:
              provider: ollama
              model: llama3
            analysis:
              provider: openai
              model: gpt-4o-mini
          providers:
            openai:
              api_key: null
              base_url: null
            ollama:
              host: null
    """
    llm_config = config.get("llm", {})
    profiles = llm_config.get("profiles", {})
    providers = llm_config.get("providers", {})

    def _make_client(profile_name: str) -> BaseLLMClient:
        profile = profiles.get(profile_name, {})
        provider = profile.get("provider", "ollama").lower()
        model = profile.get("model")
        provider_cfg = providers.get(provider, {})

        logger.info(f"LLM 프로파일 '{profile_name}': provider={provider}, model={model}")

        if provider == "openai":
            return OpenAIClient(
                model=model or provider_cfg.get("model", "gpt-4o-mini"),
                api_key=provider_cfg.get("api_key"),
                base_url=provider_cfg.get("base_url"),
            )
        else:
            return OllamaClient(
                model=model or provider_cfg.get("model", "llama3"),
                host=provider_cfg.get("host") or os.getenv("OLLAMA_HOST"),
            )

    return LLMClients(
        simple=_make_client("simple"),
        roleplay=_make_client("roleplay"),
        analysis=_make_client("analysis"),
    )
