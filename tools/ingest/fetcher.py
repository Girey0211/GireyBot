"""
웹 URL 가져오기 및 텍스트 추출

trafilatura를 사용해 HTML에서 본문 텍스트와 제목을 추출합니다.
"""

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger("ingest.fetcher")

FETCH_TIMEOUT = 20.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GireyBot-Ingest/1.0)"
}


@dataclass
class FetchResult:
    url: str
    title: str
    text: str


async def fetch(url: str) -> FetchResult:
    """단일 URL을 가져와 제목과 본문 텍스트를 반환합니다."""
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        headers=HEADERS,
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

    title = _extract_title(html, url)
    text = _extract_text(html)

    if not text:
        raise ValueError(f"본문 텍스트를 추출할 수 없습니다: {url}")

    logger.info(f"완료: {url} — '{title}' ({len(text)}자)")
    return FetchResult(url=url, title=title, text=text)


async def fetch_many(urls: list[str]) -> list[FetchResult | Exception]:
    """여러 URL을 순서대로 가져옵니다. 실패한 URL은 Exception으로 반환합니다."""
    results = []
    for url in urls:
        try:
            results.append(await fetch(url))
        except Exception as e:
            logger.warning(f"실패: {url} — {e}")
            results.append(e)
    return results


def parse_urls(raw: list[str]) -> list[str]:
    """URL 목록에서 유효한 http/https URL만 필터링하고 정규화합니다."""
    result = []
    for u in raw:
        u = u.strip().replace("\\", "")  # 쉘 이스케이프 백슬래시 제거
        if u.startswith(("http://", "https://")):
            result.append(u)
    return result


def _extract_title(html: str, fallback_url: str) -> str:
    try:
        import trafilatura
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            return meta.title.strip()
    except Exception:
        pass

    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return fallback_url.rstrip("/").split("/")[-1]


def _extract_text(html: str) -> str:
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            include_tables=True,
            include_links=False,
            include_images=False,
            no_fallback=False,
        )
        if text:
            return text.strip()
    except Exception as e:
        logger.warning(f"trafilatura 실패: {e}")
    return ""
