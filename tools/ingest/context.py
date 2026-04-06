"""
CLI 공유 컨텍스트 — config 로딩 및 컴포넌트 초기화
"""

from src.shared.config import load_default_config
from src.shared.llm import create_llm_clients, LLMClients
from src.main.memory.manager import MemoryManager
from src.main.rag.store import RAGStore
from src.main.rag.embedder import Embedder
from src.main.rag.ingest import Ingestor


class Context:
    def __init__(self):
        self.config = load_default_config()
        self.llm: LLMClients = create_llm_clients(self.config)
        self.memory = MemoryManager(self.config)
        self.store = RAGStore(self.config)
        self.embedder = Embedder(self.config)
        self.ingestor = Ingestor(self.store, self.embedder, self.config)

    async def __aenter__(self):
        await self.memory.initialize()
        await self.store.initialize()
        return self

    async def __aexit__(self, *_):
        await self.memory.close()
