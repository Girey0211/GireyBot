import argparse

from tools.ingest.context import Context


async def run(ctx: Context, args: argparse.Namespace):
    ids: list[int] = args.ids

    ok = 0
    fail = 0

    for doc_id in ids:
        doc = await ctx.memory.get_knowledge(doc_id)
        if doc is None:
            print(f"  ❌ ID {doc_id} 문서를 찾을 수 없습니다.")
            fail += 1
            continue

        await ctx.memory.delete_knowledge(doc_id)
        ctx.ingestor.forget_doc(doc_id)
        print(f"  🗑  [{doc_id}] {doc.title}")
        ok += 1

    print(f"\n완료: 삭제 {ok}개 / 실패 {fail}개")
