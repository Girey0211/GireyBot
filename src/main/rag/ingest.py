"""
문서 파싱 & 청킹 & 인덱싱

SQLite knowledge_docs 테이블 또는 data/knowledge/ 파일을
청킹하여 ChromaDB knowledge 컬렉션에 저장합니다.
"""

import hashlib
import logging
from pathlib import Path

from src.main.rag.store import RAGStore, COLLECTION_KNOWLEDGE
from src.main.rag.embedder import Embedder

logger = logging.getLogger("girey-bot.rag.ingest")

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}
MAX_CHUNK = 500     # 청크 최대 길이 (글자)
MIN_CHUNK = 80      # 이보다 짧으면 인접 청크와 병합
OVERLAP = 50        # 강제 분할 시 overlap


class Ingestor:
    """
    파일 → 청크 → 임베딩 → ChromaDB 파이프라인
    """

    def __init__(self, store: RAGStore, embedder: Embedder, config: dict):
        self.store = store
        self.embedder = embedder

        rag_cfg = config.get("llm", {}).get("rag", {})
        self.knowledge_dir = Path(
            rag_cfg.get("knowledge_dir", "data/knowledge")
        )

    # ─── 공개 API ─────────────────────────────────────────────────

    async def ingest_directory(self) -> dict:
        """knowledge_dir 내 모든 지원 파일을 인덱싱합니다."""
        if not self.store.is_available or not self.embedder.is_available:
            return {"indexed": 0, "skipped": 0, "errors": 0}

        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        files = [
            f for f in self.knowledge_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

        stats = {"indexed": 0, "skipped": 0, "errors": 0}
        for path in files:
            result = await self.ingest_file(path)
            for k, v in result.items():
                stats[k] += v

        logger.info(f"[Ingestor] 디렉토리 인덱싱 완료: {stats}")
        return stats

    async def ingest_file(self, path: Path) -> dict:
        """단일 파일을 청킹하여 ChromaDB에 저장합니다."""
        if not self.store.is_available or not self.embedder.is_available:
            return {"indexed": 0, "skipped": 0, "errors": 0}

        try:
            text = self._read_file(path)
            if not text.strip():
                return {"indexed": 0, "skipped": 1, "errors": 0}

            chunks = self._chunk_text(text)
            if not chunks:
                return {"indexed": 0, "skipped": 1, "errors": 0}

            source = str(path.relative_to(self.knowledge_dir)) if path.is_relative_to(self.knowledge_dir) else path.name

            ids = [self._chunk_id(source, i, chunk) for i, chunk in enumerate(chunks)]
            metadatas = [
                {
                    "source": source,
                    "filename": path.name,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                }
                for i in range(len(chunks))
            ]

            embeddings = await self.embedder.embed_batch(chunks)
            if not embeddings:
                return {"indexed": 0, "skipped": 0, "errors": 1}

            # 기존 source 삭제 후 재저장 (덮어쓰기)
            self.store.delete_by_source(COLLECTION_KNOWLEDGE, source)
            self.store.upsert(
                collection=COLLECTION_KNOWLEDGE,
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )
            logger.info(f"[Ingestor] 인덱싱 완료: {source} ({len(chunks)} chunks)")
            return {"indexed": len(chunks), "skipped": 0, "errors": 0}

        except Exception as e:
            logger.error(f"[Ingestor] 파일 인덱싱 실패 {path}: {e}")
            return {"indexed": 0, "skipped": 0, "errors": 1}

    async def ingest_text(self, text: str, source: str) -> dict:
        """텍스트를 직접 인덱싱합니다 (첨부 파일 없이 텍스트만 받을 때)."""
        if not self.store.is_available or not self.embedder.is_available:
            return {"indexed": 0, "skipped": 0, "errors": 0}

        chunks = self._chunk_text(text)
        if not chunks:
            return {"indexed": 0, "skipped": 1, "errors": 0}

        ids = [self._chunk_id(source, i, chunk) for i, chunk in enumerate(chunks)]
        metadatas = [
            {"source": source, "filename": source, "chunk_index": i, "total_chunks": len(chunks)}
            for i in range(len(chunks))
        ]

        embeddings = await self.embedder.embed_batch(chunks)
        if not embeddings:
            return {"indexed": 0, "skipped": 0, "errors": 1}

        self.store.delete_by_source(COLLECTION_KNOWLEDGE, source)
        self.store.upsert(
            collection=COLLECTION_KNOWLEDGE,
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
        logger.info(f"[Ingestor] 텍스트 인덱싱 완료: {source} ({len(chunks)} chunks)")
        return {"indexed": len(chunks), "skipped": 0, "errors": 0}

    async def ingest_from_db(self, memory_manager) -> dict:
        """SQLite knowledge_docs 테이블의 모든 문서를 ChromaDB에 인덱싱합니다."""
        if not self.store.is_available or not self.embedder.is_available:
            return {"indexed": 0, "skipped": 0, "errors": 0}

        docs = await memory_manager.list_knowledge()
        if not docs:
            return {"indexed": 0, "skipped": 0, "errors": 0}

        stats = {"indexed": 0, "skipped": 0, "errors": 0}
        for doc in docs:
            result = await self.ingest_knowledge_doc(doc)
            for k, v in result.items():
                stats[k] += v

        logger.info(f"[Ingestor] DB 전체 인덱싱 완료: {stats}")
        return stats

    async def ingest_knowledge_doc(self, doc) -> dict:
        """KnowledgeDoc 단건을 청킹하여 ChromaDB에 저장합니다."""
        if not self.store.is_available or not self.embedder.is_available:
            return {"indexed": 0, "skipped": 0, "errors": 0}

        try:
            source = f"db:{doc.id}"
            chunks = self._chunk_text(doc.content)
            if not chunks:
                return {"indexed": 0, "skipped": 1, "errors": 0}

            ids = [self._chunk_id(source, i, chunk) for i, chunk in enumerate(chunks)]
            metadatas = [
                {
                    "source": source,
                    "title": doc.title,
                    "category": doc.category,
                    "doc_id": doc.id,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                }
                for i in range(len(chunks))
            ]

            embeddings = await self.embedder.embed_batch(chunks)
            if not embeddings:
                return {"indexed": 0, "skipped": 0, "errors": 1}

            self.store.delete_by_source(COLLECTION_KNOWLEDGE, source)
            self.store.upsert(
                collection=COLLECTION_KNOWLEDGE,
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )
            logger.info(f"[Ingestor] DB 문서 인덱싱: [{doc.id}] {doc.title} ({len(chunks)} chunks)")
            return {"indexed": len(chunks), "skipped": 0, "errors": 0}

        except Exception as e:
            logger.error(f"[Ingestor] DB 문서 인덱싱 실패 id={doc.id}: {e}")
            return {"indexed": 0, "skipped": 0, "errors": 1}

    def forget_doc(self, doc_id: int):
        """DB 문서 ID 기준으로 ChromaDB에서 청크를 삭제합니다."""
        self.store.delete_by_source(COLLECTION_KNOWLEDGE, f"db:{doc_id}")
        logger.info(f"[Ingestor] DB 문서 벡터 삭제: id={doc_id}")

    def forget_source(self, source: str):
        """source 기준으로 knowledge 컬렉션에서 삭제합니다."""
        self.store.delete_by_source(COLLECTION_KNOWLEDGE, source)
        logger.info(f"[Ingestor] 삭제 완료: {source}")

    # ─── 내부 유틸 ───────────────────────────────────────────────

    @staticmethod
    def _read_file(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in (".md", ".txt"):
            return path.read_text(encoding="utf-8", errors="ignore")
        elif suffix == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(path))
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                logger.warning("[Ingestor] pypdf 없음 — PDF 지원 불가. 'uv add pypdf'")
                return ""
        return ""

    @staticmethod
    def _chunk_text(text: str) -> list[str]:
        """
        문단 → 문장 → 강제분할 순서로 의미 단위를 보존하며 청킹합니다.

        1단계: 빈 줄(\n\n) 기준으로 문단 분리
        2단계: MAX_CHUNK 초과 문단은 문장 단위(. ! ? \n)로 추가 분할
        3단계: MIN_CHUNK 미만 조각은 인접 조각과 병합
        4단계: 그래도 MAX_CHUNK 초과 시 OVERLAP 포함 강제 분할
        """
        import re

        text = text.strip()
        if not text:
            return []

        # 1단계: 문단 분리
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

        # 2단계: 긴 문단을 문장 단위로 분할
        pieces: list[str] = []
        for para in paragraphs:
            if len(para) <= MAX_CHUNK:
                pieces.append(para)
            else:
                # 문장 경계: 마침표/느낌표/물음표 + 공백, 또는 줄바꿈
                sentences = re.split(r"(?<=[.!?\n])\s+", para)
                buf = ""
                for sent in sentences:
                    if not sent.strip():
                        continue
                    if buf and len(buf) + len(sent) + 1 > MAX_CHUNK:
                        pieces.append(buf.strip())
                        buf = sent
                    else:
                        buf = (buf + " " + sent).strip() if buf else sent
                if buf:
                    pieces.append(buf.strip())

        # 3단계: 너무 짧은 조각을 앞 조각과 병합
        merged: list[str] = []
        for piece in pieces:
            if merged and len(piece) < MIN_CHUNK and len(merged[-1]) + len(piece) + 1 <= MAX_CHUNK:
                merged[-1] = merged[-1] + " " + piece
            else:
                merged.append(piece)

        # 4단계: 병합 후에도 MAX_CHUNK 초과 시 강제 분할 (fallback)
        final: list[str] = []
        for chunk in merged:
            if len(chunk) <= MAX_CHUNK:
                final.append(chunk)
            else:
                start = 0
                while start < len(chunk):
                    final.append(chunk[start:start + MAX_CHUNK].strip())
                    start += MAX_CHUNK - OVERLAP

        return [c for c in final if c]

    @staticmethod
    def _chunk_id(source: str, index: int, content: str) -> str:
        """결정적(deterministic) 청크 ID를 생성합니다."""
        raw = f"{source}::{index}::{content[:50]}"
        return hashlib.md5(raw.encode()).hexdigest()
