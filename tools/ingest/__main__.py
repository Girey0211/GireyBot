"""
GireyBot 지식베이스 관리 CLI

사용법:
  python -m tools.ingest <command> [options]

커맨드:
  url       웹 URL을 가져와 지식베이스에 저장
  file      로컬 파일(.md/.txt/.pdf)을 지식베이스에 저장
  list      저장된 문서 목록 조회
  view      문서 본문 내용 출력
  delete    문서 삭제 (SQLite + ChromaDB)
  reindex   SQLite → ChromaDB 전체 재인덱싱
  search    RAG 검색 진단 (유사도 점수 확인)
"""

import argparse
import asyncio
import logging
import sys

CATEGORIES = ["people", "rules", "events", "general"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ingest",
        description="GireyBot 지식베이스 관리 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 로그 출력")

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # ── url ──────────────────────────────────────────────────────
    p_url = sub.add_parser("url", help="웹 URL 인제스트")
    p_url.add_argument(
        "--urls", nargs="+", required=True,
        metavar="URL",
        help="학습할 URL (여러 개 가능)",
    )
    p_url.add_argument(
        "--category", default="general", choices=CATEGORIES,
        help="카테고리 (기본값: general)",
    )
    p_url.add_argument(
        "--title",
        default=None,
        help="제목 prefix — 지정 시 '<prefix> — <페이지 제목>' 형식으로 저장",
    )
    p_url.add_argument("--dry-run", action="store_true", help="저장 없이 미리보기만 출력")

    # ── file ─────────────────────────────────────────────────────
    p_file = sub.add_parser("file", help="로컬 파일 인제스트 (.md / .txt / .pdf)")
    p_file.add_argument("--path", required=True, help="파일 또는 디렉토리 경로")
    p_file.add_argument("--title", default=None, help="문서 제목 (파일명으로 자동 설정)")
    p_file.add_argument(
        "--category", default="general", choices=CATEGORIES,
        help="카테고리 (기본값: general)",
    )
    p_file.add_argument("--dry-run", action="store_true", help="저장 없이 미리보기만 출력")

    # ── list ─────────────────────────────────────────────────────
    p_list = sub.add_parser("list", help="저장된 문서 목록 조회")
    p_list.add_argument(
        "--category", default=None, choices=CATEGORIES,
        help="카테고리 필터 (생략 시 전체)",
    )

    # ── delete ───────────────────────────────────────────────────
    p_del = sub.add_parser("delete", help="문서 삭제 (SQLite + ChromaDB)")
    p_del.add_argument(
        "--id", dest="ids", nargs="+", type=int, required=True,
        metavar="ID",
        help="삭제할 문서 ID (여러 개 가능)",
    )

    # ── view ─────────────────────────────────────────────────────
    p_view = sub.add_parser("view", help="문서 본문 내용 출력")
    p_view.add_argument("--id", type=int, required=True, metavar="ID", help="확인할 문서 ID")

    # ── search ───────────────────────────────────────────────────
    p_search = sub.add_parser("search", help="RAG 검색 진단 (유사도 점수 확인)")
    p_search.add_argument("query", help="검색할 텍스트")
    p_search.add_argument("--top-k", type=int, default=10, help="결과 수 (기본값: 10)")

    # ── reindex ──────────────────────────────────────────────────
    sub.add_parser("reindex", help="SQLite → ChromaDB 전체 재인덱싱")

    return parser


async def _dispatch(args: argparse.Namespace):
    from tools.ingest.context import Context

    async with Context() as ctx:
        if args.command == "url":
            from tools.ingest.commands.url import run
        elif args.command == "file":
            from tools.ingest.commands.file import run
        elif args.command == "list":
            from tools.ingest.commands.list import run
        elif args.command == "delete":
            from tools.ingest.commands.delete import run
        elif args.command == "view":
            from tools.ingest.commands.view import run
        elif args.command == "search":
            from tools.ingest.commands.search import run
        elif args.command == "reindex":
            from tools.ingest.commands.reindex import run
        else:
            print(f"알 수 없는 커맨드: {args.command}", file=sys.stderr)
            sys.exit(1)

        await run(ctx, args)


def main():
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    asyncio.run(_dispatch(args))


if __name__ == "__main__":
    main()
