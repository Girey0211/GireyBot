import argparse

from tools.ingest.context import Context
from src.main.rag.store import COLLECTION_KNOWLEDGE


async def run(ctx: Context, args: argparse.Namespace):
    docs = await ctx.memory.list_knowledge(category=getattr(args, "category", None))

    if not docs:
        print("저장된 문서가 없습니다.")
        return

    # ChromaDB에서 소스별 청크 수 조회
    chunk_counts: dict[int, int] = {}
    if ctx.store.is_available:
        col = ctx.store._col(COLLECTION_KNOWLEDGE)
        if col:
            try:
                result = col.get(include=["metadatas"])
                for meta in result["metadatas"]:
                    source = meta.get("source", "")
                    if source.startswith("db:"):
                        try:
                            doc_id = int(source[3:])
                            chunk_counts[doc_id] = chunk_counts.get(doc_id, 0) + 1
                        except ValueError:
                            pass
            except Exception:
                pass

    # 출력
    header = f"{'ID':>4}  {'제목':<30}  {'카테고리':<10}  {'청크':>4}  저장일"
    print(header)
    print("─" * len(header))

    for doc in docs:
        chunks = chunk_counts.get(doc.id, "?")
        date = doc.created_at[:10]
        title = doc.title[:28] + ".." if len(doc.title) > 30 else doc.title
        print(f"{doc.id:>4}  {title:<30}  {doc.category:<10}  {str(chunks):>4}  {date}")

    print(f"\n총 {len(docs)}개")
