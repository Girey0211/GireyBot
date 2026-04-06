import argparse

from tools.ingest.context import Context


async def run(ctx: Context, args: argparse.Namespace):
    doc = await ctx.memory.get_knowledge(args.id)
    if doc is None:
        print(f"❌ ID {args.id} 문서를 찾을 수 없습니다.")
        return

    print(f"[{doc.id}] {doc.title}")
    print(f"카테고리: {doc.category}  |  저장일: {doc.created_at[:10]}")
    print("─" * 60)
    print(doc.content)
