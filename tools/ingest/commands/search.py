"""
RAG 검색 진단 커맨드

저장된 벡터를 실제로 쿼리하여 유사도 점수와 함께 결과를 출력합니다.
"""
import argparse

from tools.ingest.context import Context
from src.main.rag.store import COLLECTION_KNOWLEDGE


async def run(ctx: Context, args: argparse.Namespace):
    if not ctx.store.is_available:
        print("❌ ChromaDB에 연결할 수 없습니다.")
        return

    if not ctx.embedder.is_available:
        print("❌ 임베딩 서비스에 연결할 수 없습니다.")
        return

    query = args.query
    top_k = args.top_k

    print(f'🔍 쿼리: "{query}"  (top_k={top_k}, 유사도 필터 없음)\n')

    embedding = await ctx.embedder.embed(query)
    if not embedding:
        print("❌ 임베딩 생성 실패")
        return

    # 필터 없이 전체 결과 출력 (min_similarity 무시)
    results = ctx.store.query(COLLECTION_KNOWLEDGE, embedding, top_k=top_k)

    if not results:
        print("검색 결과 없음 (컬렉션이 비어 있을 수 있습니다)")
        return

    print(f"{'순위':<4} {'유사도':>6}  {'출처':<30}  내용 미리보기")
    print("─" * 80)
    for i, h in enumerate(results, 1):
        distance = h["distance"]
        similarity = 1.0 - distance
        source = h["metadata"].get("source", "?")[:28]
        preview = h["document"].replace("\n", " ")[:50]
        flag = ""
        if similarity < 0.45:
            flag = "  ← 현재 임계값 미달"
        print(f"{i:<4} {similarity:>6.3f}  {source:<30}  {preview}…{flag}")

    retrieval_cfg = ctx.config.get("llm", {}).get("rag", {}).get("retrieval", {})
    min_sim = retrieval_cfg.get("min_similarity", 0.45)
    print(f"\n현재 min_similarity 설정: {min_sim}")
    print("※ 유사도가 낮다면 secrets.yaml의 llm.rag.retrieval.min_similarity를 낮추세요.")
