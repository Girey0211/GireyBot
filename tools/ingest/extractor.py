"""
AI 기반 정보 추출기

웹/파일에서 가져온 원문 텍스트를 LLM이 분석하여
필요한 정보만 추출·정리합니다.

설정 예시 (secrets.yaml):
  ingest:
    extraction:
      enabled: true
      prompt: |
        다음 텍스트에서 인물에 관한 핵심 정보만 추출하여 정리해줘.
        - 기본 정보 (이름, 출신, 직업 등)
        - 주요 특징 및 능력
        - 중요 행적
        불필요한 광고, 목차, 편집 안내 등은 제거해줘.
"""

import logging

from src.shared.llm import BaseLLMClient

logger = logging.getLogger("ingest.extractor")

DEFAULT_PROMPT = """다음은 웹 페이지에서 가져온 텍스트입니다.
중요한 정보만 추출하여 깔끔하게 정리해줘.
광고, 목차, 편집 안내, 외부 링크 목록 같은 불필요한 내용은 제거해줘.
원문의 의미를 바꾸지 말고, 정보를 빠뜨리지 않도록 해줘."""


class Extractor:
    """
    LLM을 사용해 원문 텍스트에서 핵심 정보를 추출합니다.

    config.ingest.extraction.enabled = false 이면 원문을 그대로 반환합니다.
    """

    def __init__(self, llm: BaseLLMClient, config: dict):
        self.llm = llm
        ext_cfg = config.get("tools", {}).get("ingest", {}).get("extraction", {})
        self.enabled: bool = ext_cfg.get("enabled", True)
        self.prompt: str = ext_cfg.get("prompt", DEFAULT_PROMPT).strip()

    async def extract(self, text: str, title: str = "") -> str:
        """
        텍스트에서 핵심 정보를 추출합니다.

        enabled=False 이면 원문을 그대로 반환합니다.
        LLM 호출 실패 시 원문을 반환합니다.
        """
        if not self.enabled:
            return text

        if not self.llm.is_available:
            logger.warning("[Extractor] LLM 사용 불가 — 원문 그대로 저장")
            return text

        subject = f" (제목: {title})" if title else ""
        user_prompt = f"{self.prompt}\n\n---\n{text}"

        try:
            response = await self.llm.chat(
                prompt=user_prompt,
                system_prompt=f"너는 텍스트에서 핵심 정보를 추출·정리하는 도우미야{subject}.",
            )
            if response.available and response.content.strip():
                logger.info(f"[Extractor] 추출 완료{subject}: {len(text)}자 → {len(response.content)}자")
                return response.content.strip()
            else:
                logger.warning(f"[Extractor] LLM 응답 없음 — 원문 그대로 저장")
                return text
        except Exception as e:
            logger.error(f"[Extractor] 추출 실패 — 원문 그대로 저장: {e}")
            return text
