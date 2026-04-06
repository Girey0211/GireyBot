"""
임베딩 생성기

OpenAI text-embedding-3-small 또는 Ollama nomic-embed-text를 사용해
텍스트를 벡터로 변환합니다.
"""

import logging
from typing import Sequence

logger = logging.getLogger("girey-bot.rag.embedder")


class Embedder:
    """
    텍스트 → 벡터 임베딩 변환기.

    config 예시:
        rag:
          embedding:
            provider: openai          # openai | ollama
            model: text-embedding-3-small
    """

    def __init__(self, config: dict):
        rag_cfg = config.get("llm", {}).get("rag", {})
        emb_cfg = rag_cfg.get("embedding", {})

        self.provider: str = emb_cfg.get("provider", "openai")
        self.model: str = emb_cfg.get(
            "model",
            "text-embedding-3-small" if self.provider == "openai" else "nomic-embed-text",
        )
        self._client = None
        self._available = False

        self._init(config)

    def _init(self, config: dict):
        if self.provider == "openai":
            self._init_openai(config)
        elif self.provider == "ollama":
            self._init_ollama(config)
        else:
            logger.error(f"[Embedder] 지원하지 않는 provider: {self.provider}")

    def _init_openai(self, config: dict):
        try:
            import os
            from openai import AsyncOpenAI

            api_key = (
                config.get("llm", {}).get("providers", {}).get("openai", {}).get("api_key")
                or os.getenv("OPENAI_API_KEY")
            )
            base_url = config.get("llm", {}).get("providers", {}).get("openai", {}).get("base_url")

            if not api_key:
                logger.warning("[Embedder] OpenAI API 키 없음 — 임베딩 비활성화")
                return

            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url

            self._client = AsyncOpenAI(**kwargs)
            self._available = True
            logger.info(f"[Embedder] OpenAI 초기화 완료: {self.model}")
        except ImportError:
            logger.error("[Embedder] openai 패키지 없음")
        except Exception as e:
            logger.error(f"[Embedder] OpenAI 초기화 실패: {e}")

    def _init_ollama(self, config: dict):
        try:
            import ollama

            host = config.get("llm", {}).get("providers", {}).get("ollama", {}).get("host", "http://localhost:11434")
            self._ollama_host = host
            self._available = True
            logger.info(f"[Embedder] Ollama 초기화 완료: {self.model} @ {host}")
        except ImportError:
            logger.error("[Embedder] ollama 패키지 없음")
        except Exception as e:
            logger.error(f"[Embedder] Ollama 초기화 실패: {e}")

    @property
    def is_available(self) -> bool:
        return self._available

    async def embed(self, text: str) -> list[float]:
        """단일 텍스트를 임베딩합니다."""
        results = await self.embed_batch([text])
        return results[0] if results else []

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """여러 텍스트를 배치 임베딩합니다."""
        if not self._available:
            return []

        texts = [t.replace("\n", " ") for t in texts]

        if self.provider == "openai":
            return await self._embed_openai(texts)
        elif self.provider == "ollama":
            return await self._embed_ollama(texts)
        return []

    async def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.embeddings.create(
                model=self.model,
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"[Embedder] OpenAI 임베딩 실패: {e}")
            return []

    async def _embed_ollama(self, texts: list[str]) -> list[list[float]]:
        try:
            import ollama

            client = ollama.AsyncClient(host=self._ollama_host)
            results = []
            for text in texts:
                resp = await client.embeddings(model=self.model, prompt=text)
                results.append(resp["embedding"])
            return results
        except Exception as e:
            logger.error(f"[Embedder] Ollama 임베딩 실패: {e}")
            return []
