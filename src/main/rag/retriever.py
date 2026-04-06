"""
RAG 검색기

사용자 메시지를 임베딩 → ChromaDB 검색 → 관련 컨텍스트 문자열 반환
"""

import logging

from src.main.rag.store import RAGStore, COLLECTION_KNOWLEDGE, COLLECTION_MEMORY
from src.main.rag.embedder import Embedder

logger = logging.getLogger("girey-bot.rag.retriever")


class Retriever:
    """
    쿼리 → 관련 청크 → 컨텍스트 문자열 변환기

    config 예시:
        rag:
          retrieval:
            top_k: 3
            min_similarity: 0.7   # cosine distance 기준 (낮을수록 유사)
    """

    def __init__(self, store: RAGStore, embedder: Embedder, config: dict):
        self.store = store
        self.embedder = embedder

        retrieval_cfg = config.get("llm", {}).get("rag", {}).get("retrieval", {})
        self.top_k: int = retrieval_cfg.get("top_k", 3)
        # ChromaDB cosine distance: 0 = 동일, 2 = 반대
        # min_similarity 0.7 → distance < 0.3
        self.max_distance: float = 1.0 - retrieval_cfg.get("min_similarity", 0.45)

    @property
    def is_available(self) -> bool:
        return self.store.is_available and self.embedder.is_available

    async def query(self, text: str, top_k: int | None = None) -> str:
        """
        knowledge + memory 컬렉션에서 관련 청크를 검색하여
        LLM 컨텍스트용 문자열로 반환합니다.

        Returns:
            "" — RAG 비활성화 또는 관련 청크 없음
            "## 관련 정보\n..." — 관련 청크 있음
        """
        if not self.is_available:
            logger.info(
                f"[RAG] 검색 불가 — store={self.store.is_available}, embedder={self.embedder.is_available}"
            )
            return ""

        top_k = top_k or self.top_k
        query_preview = text[:60].replace("\n", " ")

        embedding = await self.embedder.embed(text)
        if not embedding:
            logger.info(f"[RAG] 임베딩 생성 실패: '{query_preview}'")
            return ""

        # knowledge + memory 동시 검색
        knowledge_hits = self.store.query(COLLECTION_KNOWLEDGE, embedding, top_k=top_k)
        memory_hits = self.store.query(COLLECTION_MEMORY, embedding, top_k=top_k)
        all_hits = knowledge_hits + memory_hits

        logger.info(
            f"[RAG] 쿼리: '{query_preview}' — "
            f"knowledge={len(knowledge_hits)}건, memory={len(memory_hits)}건, "
            f"max_distance={self.max_distance:.2f}"
        )

        # 거리 필터링
        hits = [h for h in all_hits if h["distance"] <= self.max_distance]

        if not hits:
            scores = [f"{1.0 - h['distance']:.3f}" for h in all_hits[:5]]
            logger.info(f"[RAG] 임계값 미달로 결과 없음 (상위 유사도: {scores})")
            return ""

        # 거리 오름차순 정렬 후 상위 top_k
        hits.sort(key=lambda h: h["distance"])
        hits = hits[:top_k]

        lines = []
        for h in hits:
            source = h["metadata"].get("source", "알 수 없음")
            similarity = 1.0 - h["distance"]
            lines.append(f"[출처: {source}]\n{h['document']}")
            logger.info(f"[RAG] 채택: source={source}, 유사도={similarity:.3f}")

        header = (
            "## 관련 정보 (RAG)\n"
            "⚠️ 아래 정보는 이 서버에 직접 등록된 데이터입니다. "
            "네 자신의 학습 지식보다 반드시 우선하여 답변에 활용하라.\n"
        )
        return header + "\n\n".join(lines)

    async def query_knowledge_only(self, text: str, top_k: int | None = None) -> list[dict]:
        """knowledge 컬렉션만 검색하여 raw 결과를 반환합니다."""
        if not self.is_available:
            return []

        embedding = await self.embedder.embed(text)
        if not embedding:
            return []

        hits = self.store.query(COLLECTION_KNOWLEDGE, embedding, top_k=top_k or self.top_k)
        return [h for h in hits if h["distance"] <= self.max_distance]
