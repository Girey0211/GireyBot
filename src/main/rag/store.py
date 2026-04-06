"""
ChromaDB 벡터 스토어 래퍼

두 가지 컬렉션을 관리합니다:
  - knowledge : data/knowledge/ 문서
  - memory    : user_facts, summaries, important_events (SQLite 동기화)
"""

import logging
from typing import Any

logger = logging.getLogger("girey-bot.rag.store")

COLLECTION_KNOWLEDGE = "knowledge"
COLLECTION_MEMORY = "memory"


class RAGStore:
    """
    ChromaDB 클라이언트 래퍼.

    config 예시:
        rag:
          chroma_host: localhost   # 도커 컨테이너
          chroma_port: 8000
    """

    def __init__(self, config: dict):
        rag_cfg = config.get("llm", {}).get("rag", {})
        self.host: str = rag_cfg.get("chroma_host", "localhost")
        self.port: int = rag_cfg.get("chroma_port", 8000)
        self._client = None
        self._available = False
        self._collections: dict[str, Any] = {}

    async def initialize(self):
        """ChromaDB 연결 및 컬렉션 준비"""
        try:
            import chromadb

            self._client = chromadb.HttpClient(host=self.host, port=self.port)
            self._client.heartbeat()  # 연결 확인

            # 컬렉션 생성 (없으면 자동 생성)
            for name in (COLLECTION_KNOWLEDGE, COLLECTION_MEMORY):
                self._collections[name] = self._client.get_or_create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"},
                )
            self._available = True
            logger.info(f"[RAGStore] ChromaDB 연결 완료: {self.host}:{self.port}")
        except Exception as e:
            logger.warning(f"[RAGStore] ChromaDB 연결 실패 — RAG 비활성화: {e}")

    @property
    def is_available(self) -> bool:
        return self._available

    def _col(self, collection: str):
        return self._collections.get(collection)

    # ─── 추가 ────────────────────────────────────────────────────

    def upsert(
        self,
        collection: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict] | None = None,
    ):
        """벡터 + 문서를 upsert합니다."""
        if not self._available:
            return
        col = self._col(collection)
        if col is None:
            return
        try:
            col.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas or [{} for _ in ids],
            )
        except Exception as e:
            logger.error(f"[RAGStore] upsert 실패: {e}")

    # ─── 검색 ────────────────────────────────────────────────────

    def query(
        self,
        collection: str,
        query_embedding: list[float],
        top_k: int = 3,
        where: dict | None = None,
    ) -> list[dict]:
        """
        유사도 검색.

        Returns:
            [{"id": ..., "document": ..., "metadata": ..., "distance": ...}, ...]
        """
        if not self._available:
            return []
        col = self._col(collection)
        if col is None:
            return []
        try:
            kwargs = {
                "query_embeddings": [query_embedding],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where

            results = col.query(**kwargs)
            items = []
            for i, doc_id in enumerate(results["ids"][0]):
                items.append({
                    "id": doc_id,
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                })
            return items
        except Exception as e:
            logger.error(f"[RAGStore] query 실패: {e}")
            return []

    # ─── 삭제 ────────────────────────────────────────────────────

    def delete_by_source(self, collection: str, source: str):
        """source 메타데이터 기준으로 문서를 삭제합니다."""
        if not self._available:
            return
        col = self._col(collection)
        if col is None:
            return
        try:
            col.delete(where={"source": source})
            logger.info(f"[RAGStore] 삭제 완료: collection={collection}, source={source}")
        except Exception as e:
            logger.error(f"[RAGStore] 삭제 실패: {e}")

    def delete_by_ids(self, collection: str, ids: list[str]):
        """ID 목록으로 문서를 삭제합니다."""
        if not self._available or not ids:
            return
        col = self._col(collection)
        if col is None:
            return
        try:
            col.delete(ids=ids)
        except Exception as e:
            logger.error(f"[RAGStore] ID 삭제 실패: {e}")

    # ─── 목록 조회 ───────────────────────────────────────────────

    def list_sources(self, collection: str) -> list[str]:
        """컬렉션에 저장된 고유 source 목록을 반환합니다."""
        if not self._available:
            return []
        col = self._col(collection)
        if col is None:
            return []
        try:
            result = col.get(include=["metadatas"])
            sources = {m.get("source", "") for m in result["metadatas"] if m.get("source")}
            return sorted(sources)
        except Exception as e:
            logger.error(f"[RAGStore] 목록 조회 실패: {e}")
            return []

    def count(self, collection: str) -> int:
        """컬렉션의 총 청크 수를 반환합니다."""
        if not self._available:
            return 0
        col = self._col(collection)
        if col is None:
            return 0
        try:
            return col.count()
        except Exception:
            return 0
