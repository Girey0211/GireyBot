import argparse
from pathlib import Path

from tools.ingest.context import Context
from src.main.rag.ingest import SUPPORTED_EXTENSIONS

SUPPORTED = SUPPORTED_EXTENSIONS  # {".md", ".txt", ".pdf"}


async def run(ctx: Context, args: argparse.Namespace):
    root = Path(args.path)

    if root.is_dir():
        files = [f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in SUPPORTED]
    elif root.is_file():
        files = [root]
    else:
        print(f"❌ 경로를 찾을 수 없습니다: {root}")
        return

    if not files:
        print(f"❌ 지원 형식({', '.join(SUPPORTED)}) 파일이 없습니다.")
        return

    print(f"📄 {len(files)}개 파일 처리 중...\n")

    ok = 0
    skip = 0
    fail = 0

    for path in files:
        title = args.title if args.title and len(files) == 1 else path.stem

        if args.dry_run:
            print(f"  [dry-run] {path}  →  제목: {title}, 카테고리: {args.category}")
            ok += 1
            continue

        try:
            text = ctx.ingestor._read_file(path)
            if not text.strip():
                print(f"  ⏭  {path.name}  (내용 없음)")
                skip += 1
                continue

            doc_id = await ctx.memory.save_knowledge(
                title=title,
                content=text,
                category=args.category,
                author_id=None,
            )
            doc = await ctx.memory.get_knowledge(doc_id)
            stats = await ctx.ingestor.ingest_knowledge_doc(doc)
            print(f"  ✅ [{doc_id}] {title}  ({stats['indexed']}청크)")
            ok += 1
        except Exception as e:
            print(f"  ❌ 실패 {path.name}\n     {e}")
            fail += 1

    print(f"\n완료: 성공 {ok}개 / 건너뜀 {skip}개 / 실패 {fail}개")
