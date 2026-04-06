import argparse

from tools.ingest.context import Context
from tools.ingest import fetcher
from tools.ingest.extractor import Extractor


async def run(ctx: Context, args: argparse.Namespace):
    urls = fetcher.parse_urls(args.urls)
    if not urls:
        print("❌ 유효한 URL이 없습니다.")
        return

    extractor = Extractor(ctx.llm.analysis, ctx.config)
    merge = args.title is not None  # --title 지정 시 하나의 문서로 병합

    print(f"🌐 {len(urls)}개 URL 처리 중...")
    if extractor.enabled:
        print("🤖 AI 추출 활성화")
    if merge:
        print(f"📎 병합 모드: '{args.title}' 으로 저장\n")
    else:
        print()

    results = await fetcher.fetch_many(urls)

    # ── 각 URL fetch + 추출 ──────────────────────────────────────
    extracted: list[tuple[str, str]] = []  # (page_title, extracted_text)
    fail = 0

    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            print(f"  ❌ {url}\n     {result}")
            fail += 1
            continue

        print(f"  ✔ 가져옴: {result.title} ({len(result.text)}자)", end="", flush=True)
        text = await extractor.extract(result.text, title=result.title)
        print(f" → 추출 {len(text)}자")

        if args.dry_run:
            print(f"\n--- {result.title} ---\n{text[:300]}{'...' if len(text) > 300 else ''}\n")

        extracted.append((result.title, text))

    if not extracted:
        print(f"\n❌ 저장할 내용이 없습니다.")
        return

    if args.dry_run:
        print(f"\n[dry-run] 저장하지 않음.")
        return

    # ── 저장 ────────────────────────────────────────────────────
    print()
    ok = 0

    if merge:
        # 모든 URL 내용을 하나의 문서로 병합
        sections = []
        for page_title, text in extracted:
            sections.append(f"## {page_title}\n\n{text}")
        merged_content = "\n\n---\n\n".join(sections)

        doc_id = await ctx.memory.save_knowledge(
            title=args.title,
            content=merged_content,
            category=args.category,
            author_id=None,
        )
        doc = await ctx.memory.get_knowledge(doc_id)
        stats = await ctx.ingestor.ingest_knowledge_doc(doc)
        print(f"  ✅ [{doc_id}] {args.title}  ({len(extracted)}개 URL 병합, {stats['indexed']}청크)")
        ok = 1
    else:
        # URL별 개별 문서 저장
        for page_title, text in extracted:
            try:
                doc_id = await ctx.memory.save_knowledge(
                    title=page_title,
                    content=text,
                    category=args.category,
                    author_id=None,
                )
                doc = await ctx.memory.get_knowledge(doc_id)
                stats = await ctx.ingestor.ingest_knowledge_doc(doc)
                print(f"  ✅ [{doc_id}] {page_title}  ({stats['indexed']}청크)")
                ok += 1
            except Exception as e:
                print(f"  ❌ 저장 실패: {page_title}\n     {e}")
                fail += 1

    print(f"\n완료: 성공 {ok}개 / 실패 {fail}개")
