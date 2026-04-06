import argparse

from tools.ingest.context import Context


async def run(ctx: Context, args: argparse.Namespace):
    if not ctx.store.is_available:
        print("❌ ChromaDB에 연결할 수 없습니다.")
        return

    docs = await ctx.memory.list_knowledge()
    if not docs:
        print("재인덱싱할 문서가 없습니다.")
        return

    print(f"🔄 {len(docs)}개 문서 재인덱싱 중...\n")

    total_chunks = 0
    fail = 0

    for doc in docs:
        stats = await ctx.ingestor.ingest_knowledge_doc(doc)
        if stats["errors"]:
            print(f"  ❌ [{doc.id}] {doc.title}")
            fail += 1
        else:
            print(f"  ✅ [{doc.id}] {doc.title}  ({stats['indexed']}청크)")
            total_chunks += stats["indexed"]

    print(f"\n완료: {len(docs) - fail}개 문서, {total_chunks}청크 / 실패 {fail}개")
