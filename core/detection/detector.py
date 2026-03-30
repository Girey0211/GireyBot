"""
호출 감지기

Discord 메시지를 분석하여 봇 호출 여부를 판단합니다.

판정 흐름:
1. 멘션 체크 — @봇 멘션이 포함되면 즉시 감지
2. 대화 연속성 체크 — 봇이 방금 응답한 채널의 후속 메시지인지 확인
3. 1차 키워드 필터 — 봇 이름/별명/설정 키워드를 정규표현식으로 검사
4. 2차 LLM 맥락 분석 — 1차 통과 시 LLM으로 맥락 분석
"""

import re
import time
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

from core.llm.base import BaseLLMClient
from core.detection.models import (
    ActiveConversation,
    CallDetectionResult,
    CONTINUATION_TIMEOUT_SECONDS,
)

logger = logging.getLogger("girey-bot.detector")


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

        # 채널별 활성 대화 추적 (channel_id → ActiveConversation)
        self._active_conversations: dict[int, ActiveConversation] = {}

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

        escaped = [re.escape(kw) for kw in all_keywords if kw]
        if not escaped:
            return None

        pattern_str = "|".join(escaped)
        pattern = re.compile(pattern_str, re.IGNORECASE)
        logger.debug(f"키워드 패턴 생성: {pattern_str}")
        return pattern

    # ─── 대화 연속성 추적 ──────────────

    def register_active_conversation(
        self,
        channel_id: int,
        user_message: str,
        bot_response: str,
    ) -> None:
        """봇이 응답한 후 해당 채널을 활성 대화 상태로 등록합니다."""
        self._active_conversations[channel_id] = ActiveConversation(
            channel_id=channel_id,
            last_bot_response=bot_response,
            last_user_message=user_message,
            timestamp=time.time(),
        )
        logger.debug(f"활성 대화 등록: channel={channel_id}")

    def clear_active_conversation(self, channel_id: int) -> None:
        """채널의 활성 대화 상태를 해제합니다."""
        if channel_id in self._active_conversations:
            del self._active_conversations[channel_id]
            logger.debug(f"활성 대화 해제: channel={channel_id}")

    def get_active_conversation(self, channel_id: int) -> ActiveConversation | None:
        """채널의 활성 대화를 반환합니다. 타임아웃된 경우 자동 해제."""
        conv = self._active_conversations.get(channel_id)
        if conv is None:
            return None

        elapsed = time.time() - conv.timestamp
        if elapsed > CONTINUATION_TIMEOUT_SECONDS:
            logger.debug(
                f"활성 대화 타임아웃: channel={channel_id}, "
                f"elapsed={elapsed:.0f}s"
            )
            self.clear_active_conversation(channel_id)
            return None

        return conv

    async def _check_continuation(
        self,
        message: "discord.Message",
        active_conv: ActiveConversation,
    ) -> CallDetectionResult:
        """
        활성 대화가 있는 채널에서 후속 메시지의 연속성을 LLM으로 판단합니다.

        연속 대화로 판단되면 detected=True, 아니면 대화를 종료합니다.
        """
        if not self.llm_client or not self.llm_client.is_available:
            self.clear_active_conversation(message.channel.id)
            return CallDetectionResult()

        llm_result = await self.llm_client.analyze_continuation(
            new_message=message.content,
            bot_response=active_conv.last_bot_response,
        )

        if not llm_result.available:
            self.clear_active_conversation(message.channel.id)
            return CallDetectionResult(llm_response=llm_result)

        # LLM 응답에서 연속 여부 파싱
        content = llm_result.content.strip().lower()
        is_continuation = "true" in content and "false" not in content

        if is_continuation:
            logger.info(
                f"대화 연속 감지: [{message.guild.name}/#{message.channel.name}] "
                f"{message.author.display_name}"
            )
            return CallDetectionResult(
                detected=True,
                trigger_type="continuation",
                confidence=0.85,
                llm_response=llm_result,
            )
        else:
            logger.info(
                f"대화 종료 판단: [{message.guild.name}/#{message.channel.name}] "
                f"{message.author.display_name} — 맥락 불일치"
            )
            self.clear_active_conversation(message.channel.id)
            return CallDetectionResult(
                detected=False,
                trigger_type=None,
                confidence=0.0,
                llm_response=llm_result,
            )

    # ─── 채널 필터링 ──────────────

    def _is_channel_monitored(self, channel_name: str) -> bool:
        """해당 채널이 자동 감지 대상인지 확인합니다."""
        if not self.auto_detect_channels:
            return True
        return channel_name in self.auto_detect_channels

    def _check_mention(self, message: "discord.Message") -> bool:
        """메시지에 봇 멘션이 포함되어 있는지 확인합니다."""
        return any(user.id == self.bot_id for user in message.mentions)

    def _check_keywords(self, content: str) -> str | None:
        """메시지 내용에서 키워드를 검색합니다."""
        if not self._keyword_pattern:
            return None

        match = self._keyword_pattern.search(content)
        if match:
            return match.group()
        return None

    async def detect(self, message: "discord.Message") -> CallDetectionResult:
        """메시지를 분석하여 봇 호출 여부를 판단합니다."""
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

        # ── 2단계: 대화 연속성 체크 ──
        active_conv = self.get_active_conversation(message.channel.id)
        if active_conv is not None:
            continuation_result = await self._check_continuation(message, active_conv)
            if continuation_result.detected:
                return continuation_result
            # 맥락 불일치 시 키워드 검사 단계로 계속 진행

        # ── 자동 감지가 비활성화되면 여기서 중단 ──
        if not self.auto_detect_enabled:
            return CallDetectionResult()

        # ── 채널 필터 ──
        channel_name = getattr(message.channel, "name", "")
        if not self._is_channel_monitored(channel_name):
            return CallDetectionResult()

        # ── 3단계: 1차 키워드 필터 ──
        matched_keyword = self._check_keywords(content)
        if not matched_keyword:
            return CallDetectionResult()

        logger.info(
            f"키워드 감지: [{message.guild.name}/#{message.channel.name}] "
            f"keyword='{matched_keyword}', "
            f"author={message.author.display_name}"
        )

        # ── 4단계: 키워드 매칭 확정 ──
        # 키워드가 이미 매칭됐으므로 추가 LLM 분석 없이 호출로 확정
        return CallDetectionResult(
            detected=True,
            trigger_type="keyword",
            matched_keyword=matched_keyword,
            confidence=0.7,
        )
