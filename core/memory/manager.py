"""
하이브리드 메모리 관리자

정적 메모리(MD 파일)와 동적 메모리(SQLite)를 결합하여
LLM에 전달할 컨텍스트를 빌드합니다.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from core.memory.models import (
    KST,
    Conversation,
    Summary,
    ImportantEvent,
    UserFact,
)
from core.memory.schema import SCHEMA_SQL, migrate

logger = logging.getLogger("girey-bot.memory")

# 프로젝트 루트
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class MemoryManager:
    """
    하이브리드 메모리 관리자.

    정적 메모리(MD 파일)와 동적 메모리(SQLite)를 결합하여
    LLM에 전달할 컨텍스트를 빌드합니다.
    """

    def __init__(self, config: dict):
        mem_config = config.get("memory", {})
        self.db_path = BASE_DIR / mem_config.get("db_path", "data/memory.db")
        self.persona_path = BASE_DIR / mem_config.get("persona_path", "data/persona.md")
        self.max_history = mem_config.get("max_history", 10)
        self.max_facts = mem_config.get("max_facts", 20)
        self.max_summaries = mem_config.get("max_summaries", 5)
        self.auto_extract_facts = mem_config.get("auto_extract_facts", True)
        self.retention_days = mem_config.get("retention_days", 7)
        self.cleanup_interval_hours = mem_config.get("cleanup_interval_hours", 24)

        self._persona_cache: str | None = None
        self._db: aiosqlite.Connection | None = None

    # ─── 초기화 / 종료 ───────────────────────────────────────

    async def initialize(self):
        """DB 초기화 및 테이블 생성"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await migrate(self._db)
        await self._db.commit()
        logger.info(f"메모리 DB 초기화 완료: {self.db_path}")

    async def close(self):
        """DB 연결 종료"""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("메모리 DB 연결 종료")

    # ─── 정적 메모리 (MD 파일) ───────────────────────────────

    def load_persona(self) -> str:
        """페르소나 MD 파일을 로드합니다. 캐시 사용."""
        if self._persona_cache is not None:
            return self._persona_cache

        if self.persona_path.exists():
            self._persona_cache = self.persona_path.read_text(encoding="utf-8")
            logger.info(f"페르소나 로드 완료: {self.persona_path}")
        else:
            self._persona_cache = ""
            logger.warning(f"페르소나 파일 없음: {self.persona_path}")

        return self._persona_cache

    # ─── 대화 저장 / 조회 ────────────────────────────────────

    async def save_conversation(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
        user_name: str,
        user_message: str,
        bot_response: str,
        channel_name: str = "",
        reaction_count: int = 0,
    ) -> int:
        """대화를 저장하고 새 레코드의 ID를 반환합니다."""
        now = datetime.now(KST).isoformat()
        async with self._db.execute(
            """
            INSERT INTO conversations
                (guild_id, channel_id, channel_name, user_id, user_name,
                 user_message, bot_response, reaction_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, channel_name, user_id, user_name,
             user_message, bot_response, reaction_count, now),
        ) as cursor:
            await self._db.commit()
            logger.debug(f"대화 저장 완료: user={user_name}, channel=#{channel_name}({channel_id})")
            return cursor.lastrowid

    async def get_recent_conversations(
        self,
        channel_id: int,
        limit: int | None = None,
    ) -> list[Conversation]:
        """채널의 최근 대화 기록을 조회합니다."""
        limit = limit or self.max_history
        async with self._db.execute(
            """
            SELECT * FROM conversations
            WHERE channel_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (channel_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Conversation(**dict(r)) for r in reversed(rows)]

    async def get_conversation_session(
        self,
        channel_id: int,
        gap_minutes: int = 10,
        max_messages: int = 100,
    ) -> list[Conversation]:
        """현재 대화 세션을 조회합니다.

        가장 최근 메시지부터 역순으로 탐색하여,
        연속된 두 메시지 사이에 gap_minutes 이상 공백이 있으면
        그 지점에서 세션을 끊습니다.

        Returns:
            시간순(오래된 것 먼저)으로 정렬된 대화 리스트
        """
        async with self._db.execute(
            """
            SELECT * FROM conversations
            WHERE channel_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (channel_id, max_messages),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return []

        conversations = [Conversation(**dict(r)) for r in rows]
        gap = timedelta(minutes=gap_minutes)

        # 최신 메시지부터 역순으로 탐색하며 갭 탐지
        session = [conversations[0]]
        for i in range(1, len(conversations)):
            current_time = datetime.fromisoformat(conversations[i - 1].created_at)
            prev_time = datetime.fromisoformat(conversations[i].created_at)
            if current_time - prev_time > gap:
                break
            session.append(conversations[i])

        session.reverse()  # 시간순 정렬
        return session

    # ─── 유저 팩트 ───────────────────────────────────────────

    async def learn_fact(
        self,
        guild_id: int,
        user_id: int,
        fact: str,
        category: str = "info",
        source_message_id: int | None = None,
    ) -> int:
        """유저에 대한 새로운 사실을 학습합니다."""
        now = datetime.now(KST).isoformat()
        async with self._db.execute(
            """
            INSERT INTO user_facts
                (guild_id, user_id, fact, category, source_message_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, fact, category, source_message_id, now),
        ) as cursor:
            await self._db.commit()
            logger.debug(f"팩트 학습: user={user_id}, fact={fact[:50]}")
            return cursor.lastrowid

    async def get_user_facts(
        self,
        user_id: int,
        limit: int | None = None,
    ) -> list[UserFact]:
        """유저에 대해 학습한 사실을 조회합니다."""
        limit = limit or self.max_facts
        async with self._db.execute(
            """
            SELECT * FROM user_facts
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [UserFact(**dict(r)) for r in rows]

    # ─── 중요 이벤트 ────────────────────────────────────────

    async def save_important_event(
        self,
        guild_id: int,
        channel_id: int,
        event_title: str,
        description: str,
        participants: list[str] | None = None,
        importance: str = "high",
        occurred_at: str | None = None,
    ) -> int:
        """중요 이벤트를 영구 저장합니다."""
        now = datetime.now(KST).isoformat()
        participants_json = json.dumps(participants or [], ensure_ascii=False)
        async with self._db.execute(
            """
            INSERT INTO important_events
                (guild_id, channel_id, event_title, description,
                 participants, importance, occurred_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, event_title, description,
             participants_json, importance, occurred_at or now, now),
        ) as cursor:
            await self._db.commit()
            logger.info(f"중요 이벤트 저장: {event_title}")
            return cursor.lastrowid

    async def get_important_events(
        self,
        guild_id: int,
        limit: int = 10,
    ) -> list[ImportantEvent]:
        """서버의 중요 이벤트를 조회합니다."""
        async with self._db.execute(
            """
            SELECT * FROM important_events
            WHERE guild_id = ?
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [ImportantEvent(**dict(r)) for r in rows]

    # ─── 요약 ───────────────────────────────────────────────

    async def save_summary(
        self,
        guild_id: int,
        channel_id: int,
        summary: str,
        period_start: str,
        period_end: str,
        message_count: int,
    ) -> int:
        """대화 요약을 저장합니다."""
        now = datetime.now(KST).isoformat()
        async with self._db.execute(
            """
            INSERT INTO summaries
                (guild_id, channel_id, summary,
                 period_start, period_end, message_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, summary,
             period_start, period_end, message_count, now),
        ) as cursor:
            await self._db.commit()
            logger.info(f"요약 저장: channel={channel_id}, messages={message_count}")
            return cursor.lastrowid

    async def get_summaries(
        self,
        channel_id: int,
        limit: int | None = None,
    ) -> list[Summary]:
        """채널의 대화 요약을 조회합니다."""
        limit = limit or self.max_summaries
        async with self._db.execute(
            """
            SELECT * FROM summaries
            WHERE channel_id = ?
            ORDER BY period_end DESC
            LIMIT ?
            """,
            (channel_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Summary(**dict(r)) for r in reversed(rows)]

    # ─── 컨텍스트 빌드 ──────────────────────────────────────

    async def build_context(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
    ) -> str:
        """LLM에 전달할 메모리 컨텍스트를 조립합니다."""
        parts: list[str] = []

        # 0. 현재 시각 기준 정보
        now = datetime.now(KST)
        parts.append(
            f"## 현재 시각\n"
            f"- {now.strftime('%Y년 %m월 %d일 %A %H:%M')} (KST)"
        )

        # 1. 유저 팩트
        facts = await self.get_user_facts(user_id)
        if facts:
            fact_lines = "\n".join(f"- {f.fact}" for f in facts)
            parts.append(f"## 이 유저에 대해 기억하고 있는 것\n{fact_lines}")

        # 2. 중요 이벤트
        events = await self.get_important_events(guild_id, limit=5)
        if events:
            event_lines = "\n".join(
                f"- [{e.importance}] {e.event_title}: {e.description}"
                for e in events
            )
            parts.append(f"## 서버에서 있었던 중요한 사건\n{event_lines}")

        # 3. 최근 요약
        summaries = await self.get_summaries(channel_id)
        if summaries:
            summary_lines = "\n".join(
                f"- ({s.period_start[:10]}~{s.period_end[:10]}) {s.summary}"
                for s in summaries
            )
            parts.append(f"## 이전 대화 요약\n{summary_lines}")

        # 4. 최근 대화 기록
        history = await self.get_recent_conversations(channel_id)
        if history:
            history_lines = "\n".join(
                f"- [{self._format_timestamp(h.created_at)}] {h.user_name}: {h.user_message}\n  봇: {h.bot_response}"
                for h in history
            )
            parts.append(f"## 최근 대화 기록\n{history_lines}")

        if not parts:
            return ""

        return "\n\n".join(parts)

    # ─── 팩트 자동 추출 ─────────────────────────────────────

    async def extract_and_save_facts(
        self,
        llm_client,
        guild_id: int,
        user_id: int,
        user_message: str,
        bot_response: str,
        source_message_id: int | None = None,
    ):
        """LLM을 사용하여 대화에서 유저 팩트를 자동 추출합니다."""
        if not self.auto_extract_facts:
            return
        if not llm_client.is_available:
            return

        try:
            result = await llm_client.chat(
                prompt=(
                    f"유저 메시지: {user_message}\n"
                    f"봇 응답: {bot_response}"
                ),
                system_prompt=(
                    "이 대화에서 유저에 대해 기억해둘 만한 사실을 추출하세요.\n"
                    "사실이 없으면 정확히 'NONE'이라고만 답하세요.\n"
                    "사실이 있으면 JSON 배열로 답하세요:\n"
                    '[{"fact": "사실 내용", "category": "preference|info|request"}]\n'
                    "주의: 일반적인 대화 내용은 사실이 아닙니다. "
                    "유저의 선호, 특성, 요청사항 등 기억해야 할 것만 추출하세요."
                ),
            )

            if not result.available or not result.content:
                return

            content = result.content.strip()
            if content.upper() == "NONE":
                return

            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            facts = json.loads(content)
            if not isinstance(facts, list):
                return

            for fact_data in facts:
                if isinstance(fact_data, dict) and "fact" in fact_data:
                    await self.learn_fact(
                        guild_id=guild_id,
                        user_id=user_id,
                        fact=fact_data["fact"],
                        category=fact_data.get("category", "info"),
                        source_message_id=source_message_id,
                    )

        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"팩트 추출 실패 (무시): {e}")

    # ─── 메모리 정리 (Cleanup) ───────────────────────────────

    async def cleanup(self, llm_client) -> dict:
        """
        TTL 만료 대화를 정리합니다.

        1. 만료 대화를 채널별로 그룹핑
        2. LLM으로 중요 이벤트 추출 → important_events에 영구 저장
        3. 나머지를 주제 단위로 요약 → summaries에 저장
        4. 원본 대화 삭제
        """
        cutoff = (
            datetime.now(KST) - timedelta(days=self.retention_days)
        ).isoformat()

        stats = {"channels": 0, "deleted": 0, "events": 0, "summaries": 0}

        async with self._db.execute(
            """
            SELECT * FROM conversations
            WHERE created_at < ?
            ORDER BY channel_id, created_at
            """,
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            logger.info("정리할 만료 대화 없음")
            return stats

        channels: dict[int, list[dict]] = {}
        for row in rows:
            row_dict = dict(row)
            ch_id = row_dict["channel_id"]
            channels.setdefault(ch_id, []).append(row_dict)

        stats["channels"] = len(channels)

        for channel_id, conversations in channels.items():
            guild_id = conversations[0]["guild_id"]
            conv_count = len(conversations)

            conv_text = "\n".join(
                f"[{c['created_at']}] {c['user_name']}: {c['user_message']}"
                f" (리액션: {c['reaction_count']})"
                for c in conversations
            )

            if llm_client and llm_client.is_available:
                await self._extract_important_events(
                    llm_client, guild_id, channel_id, conv_text, conversations
                )
                await self._summarize_conversations(
                    llm_client, guild_id, channel_id, conv_text, conversations
                )
            else:
                logger.warning("LLM 미사용 — 요약 없이 대화 삭제")

            conv_ids = [c["id"] for c in conversations]
            placeholders = ",".join("?" * len(conv_ids))
            await self._db.execute(
                f"DELETE FROM conversations WHERE id IN ({placeholders})",
                conv_ids,
            )
            await self._db.commit()

            stats["deleted"] += conv_count
            logger.info(
                f"채널 {channel_id} 정리 완료: "
                f"{conv_count}개 대화 삭제"
            )

        return stats

    async def _extract_important_events(
        self,
        llm_client,
        guild_id: int,
        channel_id: int,
        conv_text: str,
        conversations: list[dict],
    ):
        """만료 대화에서 중요 이벤트를 추출합니다."""
        high_reaction = [
            c for c in conversations if c["reaction_count"] >= 3
        ]
        reaction_hint = ""
        if high_reaction:
            reaction_hint = (
                "\n\n[참고: 리액션(반응)이 많은 메시지 — 커뮤니티 관심도 높음]\n"
                + "\n".join(
                    f"- {c['user_name']}: {c['user_message']} "
                    f"(리액션 {c['reaction_count']}개)"
                    for c in high_reaction
                )
            )

        unique_users = set(c["user_name"] for c in conversations)
        participant_hint = (
            f"\n\n[참고: 이 대화에 참여한 유저 수: {len(unique_users)}명 "
            f"({', '.join(list(unique_users)[:10])})]"
        )

        try:
            result = await llm_client.chat(
                prompt=conv_text + reaction_hint + participant_hint,
                system_prompt=(
                    "아래 Discord 대화 기록을 분석하여 중요한 사건이나 이벤트를 추출하세요.\n\n"
                    "중요한 사건의 기준:\n"
                    "- 서버 규칙 변경, 장애 발생, 중요 결정 등 내용적으로 중요한 것\n"
                    "- 리액션(반응)이 많이 달린 메시지의 주제 (커뮤니티 관심사)\n"
                    "- 여러 유저가 반복적으로 언급한 토픽\n\n"
                    "중요한 사건이 없으면 정확히 'NONE'이라고만 답하세요.\n"
                    "있으면 JSON 배열로 답하세요:\n"
                    '[{"event_title": "제목", "description": "설명", '
                    '"participants": ["유저1", "유저2"], '
                    '"importance": "high|critical", '
                    '"occurred_at": "YYYY-MM-DDTHH:MM:SS"}]'
                ),
            )

            if not result.available or not result.content:
                return

            content = result.content.strip()
            if content.upper() == "NONE":
                return

            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            events = json.loads(content)
            if not isinstance(events, list):
                return

            for event in events:
                if isinstance(event, dict) and "event_title" in event:
                    await self.save_important_event(
                        guild_id=guild_id,
                        channel_id=channel_id,
                        event_title=event["event_title"],
                        description=event.get("description", ""),
                        participants=event.get("participants", []),
                        importance=event.get("importance", "high"),
                        occurred_at=event.get("occurred_at"),
                    )

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"중요 이벤트 추출 실패: {e}")

    async def _summarize_conversations(
        self,
        llm_client,
        guild_id: int,
        channel_id: int,
        conv_text: str,
        conversations: list[dict],
    ):
        """만료 대화를 주제 단위로 요약합니다."""
        try:
            result = await llm_client.chat(
                prompt=conv_text,
                system_prompt=(
                    "아래 Discord 대화 기록을 이벤트/주제 단위로 간결하게 요약하세요.\n"
                    "핵심 내용만 2~3문장으로 축약하세요.\n"
                    "불필요한 잡담은 제외하세요.\n"
                    "반드시 한국어로 답하세요."
                ),
            )

            if not result.available or not result.content:
                return

            period_start = conversations[0]["created_at"]
            period_end = conversations[-1]["created_at"]

            await self.save_summary(
                guild_id=guild_id,
                channel_id=channel_id,
                summary=result.content.strip(),
                period_start=period_start,
                period_end=period_end,
                message_count=len(conversations),
            )

        except Exception as e:
            logger.warning(f"대화 요약 실패: {e}")

    # ─── 유틸 ───────────────────────────────────────────────

    @staticmethod
    def _format_timestamp(iso_str: str) -> str:
        """ISO 타임스탬프를 읽기 쉬운 형식으로 변환합니다."""
        try:
            dt = datetime.fromisoformat(iso_str)
            now = datetime.now(KST)
            if dt.date() == now.date():
                return f"오늘 {dt.strftime('%H:%M')}"
            elif dt.date() == (now - timedelta(days=1)).date():
                return f"어제 {dt.strftime('%H:%M')}"
            else:
                return dt.strftime("%m/%d %H:%M")
        except (ValueError, TypeError):
            return iso_str[:16]

    async def get_stats(self) -> dict:
        """메모리 통계를 반환합니다."""
        stats = {}
        for table in ("conversations", "summaries", "important_events", "user_facts"):
            async with self._db.execute(
                f"SELECT COUNT(*) FROM {table}"
            ) as cursor:
                row = await cursor.fetchone()
                stats[table] = row[0]
        return stats
