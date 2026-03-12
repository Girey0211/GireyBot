"""
메모리 데이터 모델
"""

from dataclasses import dataclass
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9))


@dataclass
class Conversation:
    """대화 기록"""
    id: int
    guild_id: int
    channel_id: int
    channel_name: str
    user_id: int
    user_name: str
    user_message: str
    bot_response: str
    reaction_count: int
    created_at: str


@dataclass
class Summary:
    """축약된 대화 요약"""
    id: int
    guild_id: int
    channel_id: int
    summary: str
    period_start: str
    period_end: str
    message_count: int
    created_at: str


@dataclass
class ImportantEvent:
    """중요 사건/이벤트"""
    id: int
    guild_id: int
    channel_id: int
    event_title: str
    description: str
    participants: str  # JSON 배열 문자열
    importance: str  # "high" | "critical"
    occurred_at: str
    created_at: str


@dataclass
class UserFact:
    """유저별 학습된 사실"""
    id: int
    guild_id: int
    user_id: int
    fact: str
    category: str  # "preference" | "info" | "request"
    source_message_id: int | None
    created_at: str
