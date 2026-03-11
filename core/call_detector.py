"""
호출 감지 모듈

Discord 메시지를 분석하여 봇 호출 여부를 판단합니다.

판정 흐름:
1. 멘션 체크 — @봇 멘션이 포함되면 즉시 감지
2. 1차 키워드 필터 — 봇 이름/별명/설정 키워드를 정규표현식으로 검사
3. 2차 LLM 맥락 분석 — 1차 통과 시 Ollama로 맥락 분석 (현재 스텁)
"""

import re
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

from core.llm_client import BaseLLMClient, LLMResponse

logger = logging.getLogger("girey-bot.detector")


@dataclass
class CallDetectionResult:
    """호출 감지 결과"""

    detected: bool = False
    trigger_type: str | None = None  # "mention" | "keyword" | "auto_detect"
    matched_keyword: str | None = None
    confidence: float = 0.0
    llm_response: LLMResponse | None = None


class CallDetector:
    """
    메시지를 분석하여 봇 호출 여부를 판단하는 감지기.

    Args:
        bot_id: 봇의 Discord User ID
        bot_names: 봇 이름/별명 목록 (예: ["기리봇", "girey"])
        keywords: 추가 감지 키워드 목록
        llm_client: LLM 클라이언트 (2차 맥락 분석용)
        auto_detect_enabled: 자동 감지 활성화 여부
        auto_detect_channels: 자동 감지 대상 채널 목록 (빈 리스트 = 전체)
    """

    def __init__(
        self,
        bot_id: int,
        bot_names: list[str],
        keywords: list[str] | None = None,
        llm_client: BaseLLMClient | None = None,
        auto_detect_enabled: bool = True,
        auto_detect_channels: list[str] | None = None,
    ):
        self.bot_id = bot_id
        self.bot_names = bot_names
        self.keywords = keywords or []
        self.llm_client = llm_client
        self.auto_detect_enabled = auto_detect_enabled
        self.auto_detect_channels = auto_detect_channels or []

        # 1차 필터용 정규표현식 컴파일
        self._keyword_pattern = self._build_keyword_pattern()

        logger.info(
            f"CallDetector 초기화 — "
            f"bot_names={bot_names}, "
            f"keywords={self.keywords}, "
            f"auto_detect={auto_detect_enabled}"
        )

    def _build_keyword_pattern(self) -> re.Pattern | None:
        """봇 이름 + 키워드를 결합한 정규표현식 패턴을 생성합니다."""
        all_keywords = self.bot_names + self.keywords
        if not all_keywords:
            return None

        # 각 키워드를 이스케이프하고 OR로 결합
        escaped = [re.escape(kw) for kw in all_keywords if kw]
        if not escaped:
            return None

        pattern_str = "|".join(escaped)
        pattern = re.compile(pattern_str, re.IGNORECASE)
        logger.debug(f"키워드 패턴 생성: {pattern_str}")
        return pattern

    def _is_channel_monitored(self, channel_name: str) -> bool:
        """해당 채널이 자동 감지 대상인지 확인합니다."""
        # 빈 리스트 = 전체 채널 감시
        if not self.auto_detect_channels:
            return True
        return channel_name in self.auto_detect_channels

    def _check_mention(self, message: "discord.Message") -> bool:
        """메시지에 봇 멘션이 포함되어 있는지 확인합니다."""
        # message.mentions에 봇이 포함되어 있는지 체크
        return any(user.id == self.bot_id for user in message.mentions)

    def _check_keywords(self, content: str) -> str | None:
        """
        메시지 내용에서 키워드를 검색합니다.

        Returns:
            매칭된 키워드 문자열, 없으면 None
        """
        if not self._keyword_pattern:
            return None

        match = self._keyword_pattern.search(content)
        if match:
            return match.group()
        return None

    async def detect(self, message: "discord.Message") -> CallDetectionResult:
        """
        메시지를 분석하여 봇 호출 여부를 판단합니다.

        Args:
            message: Discord 메시지 객체

        Returns:
            CallDetectionResult: 감지 결과
        """
        content = message.content.strip()
        if not content:
            return CallDetectionResult()

        # ── 1단계: 멘션 체크 ──
        if self._check_mention(message):
            logger.info(
                f"멘션 감지: [{message.guild.name}/#{message.channel.name}] "
                f"{message.author.display_name}"
            )
            return CallDetectionResult(
                detected=True,
                trigger_type="mention",
                confidence=1.0,
            )

        # ── 자동 감지가 비활성화되면 여기서 중단 ──
        if not self.auto_detect_enabled:
            return CallDetectionResult()

        # ── 채널 필터 ──
        channel_name = getattr(message.channel, "name", "")
        if not self._is_channel_monitored(channel_name):
            return CallDetectionResult()

        # ── 2단계: 1차 키워드 필터 ──
        matched_keyword = self._check_keywords(content)
        if not matched_keyword:
            return CallDetectionResult()

        logger.info(
            f"키워드 감지: [{message.guild.name}/#{message.channel.name}] "
            f"keyword='{matched_keyword}', "
            f"author={message.author.display_name}"
        )

        # ── 3단계: 2차 LLM 맥락 분석 ──
        if self.llm_client and self.llm_client.is_available:
            # TODO: 이전 대화 맥락도 함께 전달
            llm_result = await self.llm_client.analyze_call_intent(content)

            if llm_result.available:
                # LLM이 호출로 판단한 경우
                return CallDetectionResult(
                    detected=True,
                    trigger_type="auto_detect",
                    matched_keyword=matched_keyword,
                    confidence=0.8,  # TODO: LLM 응답에서 확신도 추출
                    llm_response=llm_result,
                )
            else:
                # LLM이 호출이 아니라고 판단
                return CallDetectionResult(
                    detected=False,
                    trigger_type=None,
                    matched_keyword=matched_keyword,
                    confidence=0.0,
                    llm_response=llm_result,
                )
        else:
            # LLM 미사용 — 키워드 매칭만으로 감지 처리
            # (Ollama 미셋업 상태에서는 여기로 옴)
            llm_result = None
            if self.llm_client:
                llm_result = await self.llm_client.analyze_call_intent(content)

            return CallDetectionResult(
                detected=True,
                trigger_type="keyword",
                matched_keyword=matched_keyword,
                confidence=0.7,
                llm_response=llm_result,
            )
