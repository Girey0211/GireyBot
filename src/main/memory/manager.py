"""
하이브리드 메모리 관리자

정적 메모리(MD 파일)와 동적 메모리(SQLite)를 결합하여
LLM에 전달할 컨텍스트를 빌드합니다.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from src.main.memory.models import (
    KST,
    Message,
    Conversation,
    Summary,
    ImportantEvent,
    KnowledgeDoc,
    UserFact,
)
from src.main.memory.schema import SCHEMA_SQL, migrate

logger = logging.getLogger("girey-bot.memory")

# 프로젝트 루트
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


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
        self.max_history = mem_config.get("max_history", 3)
        self.max_messages = mem_config.get("max_messages", 10)
        self.max_facts = mem_config.get("max_facts", 5)
        self.max_summaries = mem_config.get("max_summaries", 2)
        self.max_events = mem_config.get("max_events", 3)
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

    # ─── 일반 메시지 저장 / 조회 ─────────────────────────────

    async def save_message(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
        user_name: str,
        content: str,
        channel_name: str = "",
    ) -> int:
        """서버 내 일반 메시지를 저장합니다."""
        now = datetime.now(KST).isoformat()
        async with self._db.execute(
            """
            INSERT INTO messages
                (guild_id, channel_id, channel_name, user_id, user_name, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, channel_id, channel_name, user_id, user_name, content, now),
        ) as cursor:
            await self._db.commit()
            return cursor.lastrowid

    async def get_messages_by_channel(
        self,
        channel_id: int,
        limit: int = 30,
    ) -> list[Message]:
        """채널의 최근 메시지를 조회합니다."""
        async with self._db.execute(
            """
            SELECT * FROM messages
            WHERE channel_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (channel_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Message(**dict(r)) for r in reversed(rows)]

    async def get_messages_by_date(
        self,
        channel_id: int,
        date_start: str,
        date_end: str,
        limit: int = 100,
    ) -> list[Message]:
        """채널의 날짜 범위 메시지를 조회합니다."""
        async with self._db.execute(
            """
            SELECT * FROM messages
            WHERE channel_id = ? AND created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (channel_id, date_start, date_end, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Message(**dict(r)) for r in rows]

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

    async def get_conversations_by_date(
        self,
        guild_id: int,
        date_start: str,
        date_end: str,
        channel_id: int | None = None,
        limit: int = 50,
    ) -> list[Conversation]:
        """날짜 범위로 대화 기록을 조회합니다.

        Args:
            guild_id: 서버 ID
            date_start: 시작 날짜 (ISO format, 예: '2026-03-19T00:00:00')
            date_end: 종료 날짜 (ISO format, 예: '2026-03-20T00:00:00')
            channel_id: 특정 채널만 조회 (None이면 서버 전체)
            limit: 최대 조회 개수
        """
        if channel_id:
            query = """
                SELECT * FROM conversations
                WHERE guild_id = ? AND channel_id = ?
                  AND created_at >= ? AND created_at < ?
                ORDER BY created_at ASC
                LIMIT ?
            """
            params = (guild_id, channel_id, date_start, date_end, limit)
        else:
            query = """
                SELECT * FROM conversations
                WHERE guild_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY created_at ASC
                LIMIT ?
            """
            params = (guild_id, date_start, date_end, limit)

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [Conversation(**dict(r)) for r in rows]

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
        guild_id: int,
        limit: int | None = None,
    ) -> list[Summary]:
        """서버의 대화 요약을 조회합니다."""
        limit = limit or self.max_summaries
        async with self._db.execute(
            """
            SELECT * FROM summaries
            WHERE guild_id = ?
            ORDER BY period_end DESC
            LIMIT ?
            """,
            (guild_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Summary(**dict(r)) for r in reversed(rows)]

    # ─── 컨텍스트 빌드 ──────────────────────────────────────

    async def build_context(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
        user_message: str = "",
        retriever=None,
        rag_query: str | None = None,
    ) -> tuple[str, str]:
        """
        LLM에 전달할 메모리 컨텍스트를 조립합니다.

        Args:
            rag_query: RAG 검색에 사용할 텍스트. None이면 user_message 사용.
                       봇 이름/호출어를 제거한 순수 질문을 넘기면 검색 정확도가 높아짐.

        Returns:
            (memory_context, rag_context)
            - memory_context: 대화 기록, 팩트, 요약 등 — system prompt에 추가
            - rag_context: RAG 검색 결과 — user prompt 앞에 주입해야 효과적
        """
        parts: list[str] = []
        rag_context: str = ""

        # 유저 메시지에서 관련성 판단용 키워드 추출
        keywords = self._extract_keywords(user_message) if user_message else []

        # 0. 현재 시각 기준 정보
        now = datetime.now(KST)
        parts.append(
            f"## 현재 시각\n"
            f"- {now.strftime('%Y년 %m월 %d일 %A %H:%M')} (KST)"
        )

        # 1. 유저 팩트 — 유저 메시지와 관련 있는 것만 포함
        facts = await self.get_user_facts(user_id)
        if facts:
            relevant_facts = [f for f in facts if self._is_relevant(f.fact, keywords)]
            if relevant_facts:
                fact_lines = "\n".join(f"- {f.fact}" for f in relevant_facts)
                parts.append(f"## 이 유저에 대해 기억하고 있는 것\n{fact_lines}")
                logger.debug(f"팩트 필터: {len(relevant_facts)}/{len(facts)}개 포함")

        # 2. 중요 이벤트 — 유저 메시지와 관련 있는 것만 포함
        events = await self.get_important_events(guild_id, limit=self.max_events)
        if events:
            relevant_events = [
                e for e in events
                if self._is_relevant(f"{e.event_title} {e.description}", keywords)
            ]
            if relevant_events:
                event_lines = "\n".join(
                    f"- [{e.importance}] {e.event_title}: {e.description}"
                    for e in relevant_events
                )
                parts.append(f"## 서버에서 있었던 중요한 사건\n{event_lines}")
                logger.debug(f"이벤트 필터: {len(relevant_events)}/{len(events)}개 포함")

        # 3. 최근 요약 — 유저 메시지와 관련 있는 것만 포함
        summaries = await self.get_summaries(guild_id)
        if summaries:
            relevant_summaries = [
                s for s in summaries if self._is_relevant(s.summary, keywords)
            ]
            if relevant_summaries:
                summary_lines = "\n".join(
                    f"- ({s.period_start[:10]}~{s.period_end[:10]}) {s.summary}"
                    for s in relevant_summaries
                )
                parts.append(f"## 이전 대화 요약\n{summary_lines}")
                logger.debug(f"요약 필터: {len(relevant_summaries)}/{len(summaries)}개 포함")

        # 4. 이 채널의 최근 메시지
        messages = await self.get_messages_by_channel(channel_id, limit=self.max_messages)
        if messages:
            msg_lines = "\n".join(
                f"- [{self._format_timestamp(m.created_at)}] {m.user_name}: {m.content}"
                for m in messages
            )
            parts.append(f"## 이 채널의 최근 대화\n{msg_lines}")

        # 5. 날짜 기반 대화 조회 (유저가 과거 대화를 물어볼 때)
        if user_message:
            date_range = self._parse_date_reference(user_message, now)
            if date_range:
                date_start, date_end, label = date_range
                dated_messages = await self.get_messages_by_date(
                    channel_id=channel_id,
                    date_start=date_start.isoformat(),
                    date_end=date_end.isoformat(),
                )
                if dated_messages:
                    dated_lines = "\n".join(
                        f"- [{m.created_at[:16]}] {m.user_name}: {m.content}"
                        for m in dated_messages
                    )
                    parts.append(
                        f"## {label} 대화 기록 ({date_start.strftime('%m/%d')}~{date_end.strftime('%m/%d')}, "
                        f"총 {len(dated_messages)}건)\n{dated_lines}"
                    )
                else:
                    parts.append(f"## {label} 대화 기록\n- 해당 기간에 저장된 대화가 없습니다.")

        # 6. RAG 검색 결과 (별도 반환 — user prompt에 주입)
        if retriever is not None and user_message:
            rag_context = await retriever.query(rag_query or user_message)

        if not parts:
            return "", rag_context

        return "\n\n".join(parts), rag_context

    @staticmethod
    def _parse_date_reference(
        message: str,
        now: datetime,
    ) -> tuple[datetime, datetime, str] | None:
        """유저 메시지에서 날짜 참조를 파싱합니다.

        Returns:
            (date_start, date_end, label) 또는 None
        """
        # "N일 전" 패턴
        match = re.search(r"(\d+)\s*일\s*전", message)
        if match:
            days_ago = int(match.group(1))
            target = now - timedelta(days=days_ago)
            start = target.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return start, end, f"{days_ago}일 전"

        # 키워드 매칭
        date_keywords = {
            "어제": 1,
            "그제": 2,
            "그저께": 2,
            "엊그제": 2,
            "그끄저께": 3,
        }

        # 대화/내역 관련 키워드와 함께 쓰였는지 확인
        history_keywords = r"대화|내역|기록|히스토리|로그|말했|얘기|이야기|했던|뭐했|뭘했"
        has_history_keyword = re.search(history_keywords, message) is not None

        for keyword, days_ago in date_keywords.items():
            if keyword in message and has_history_keyword:
                target = now - timedelta(days=days_ago)
                start = target.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                return start, end, keyword

        # "오늘" 대화 조회
        if "오늘" in message and has_history_keyword:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return start, end, "오늘"

        return None

    # ─── 지식 문서 ──────────────────────────────────────────

    async def save_knowledge(
        self,
        title: str,
        content: str,
        category: str = "general",
        author_id: int | None = None,
    ) -> int:
        """지식 문서를 저장하고 ID를 반환합니다."""
        now = datetime.now(KST).isoformat()
        async with self._db.execute(
            """
            INSERT INTO knowledge_docs (title, content, category, author_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title, content, category, author_id, now, now),
        ) as cursor:
            await self._db.commit()
            logger.info(f"지식 저장: title={title}, category={category}")
            return cursor.lastrowid

    async def update_knowledge(self, doc_id: int, content: str) -> bool:
        """지식 문서 내용을 수정합니다."""
        now = datetime.now(KST).isoformat()
        async with self._db.execute(
            "UPDATE knowledge_docs SET content = ?, updated_at = ? WHERE id = ?",
            (content, now, doc_id),
        ) as cursor:
            await self._db.commit()
            return cursor.rowcount > 0

    async def delete_knowledge(self, doc_id: int) -> bool:
        """지식 문서를 삭제합니다."""
        async with self._db.execute(
            "DELETE FROM knowledge_docs WHERE id = ?",
            (doc_id,),
        ) as cursor:
            await self._db.commit()
            return cursor.rowcount > 0

    async def get_knowledge(self, doc_id: int) -> KnowledgeDoc | None:
        """ID로 지식 문서를 조회합니다."""
        async with self._db.execute(
            "SELECT * FROM knowledge_docs WHERE id = ?",
            (doc_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return KnowledgeDoc(**dict(row)) if row else None

    async def list_knowledge(
        self,
        category: str | None = None,
        limit: int = 100,
    ) -> list[KnowledgeDoc]:
        """지식 문서 목록을 조회합니다."""
        if category:
            query = "SELECT * FROM knowledge_docs WHERE category = ? ORDER BY created_at DESC LIMIT ?"
            params = (category, limit)
        else:
            query = "SELECT * FROM knowledge_docs ORDER BY created_at DESC LIMIT ?"
            params = (limit,)

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [KnowledgeDoc(**dict(r)) for r in rows]

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
        TTL 만료 메시지를 정리합니다.

        1. 만료 메시지를 서버별로 그룹핑
        2. LLM으로 중요 이벤트 추출 → important_events에 영구 저장
        3. 나머지를 주제 단위로 요약 → summaries에 저장
        4. 원본 메시지 및 대화 삭제
        """
        cutoff = (
            datetime.now(KST) - timedelta(days=self.retention_days)
        ).isoformat()

        stats = {"guilds": 0, "deleted": 0, "events": 0, "summaries": 0}

        # 만료 메시지를 서버 단위로 조회
        async with self._db.execute(
            """
            SELECT * FROM messages
            WHERE created_at < ?
            ORDER BY guild_id, created_at
            """,
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            logger.info("정리할 만료 메시지 없음")
        else:
            # 서버 단위로 그룹핑
            guilds: dict[int, list[dict]] = {}
            for row in rows:
                row_dict = dict(row)
                g_id = row_dict["guild_id"]
                guilds.setdefault(g_id, []).append(row_dict)

            stats["guilds"] = len(guilds)

            for guild_id, messages in guilds.items():
                msg_count = len(messages)

                msg_text = "\n".join(
                    f"[{m['created_at']}] #{m['channel_name'] or '?'} "
                    f"{m['user_name']}: {m['content']}"
                    for m in messages
                )

                if llm_client and llm_client.is_available:
                    await self._extract_important_events_from_messages(
                        llm_client, guild_id, msg_text, messages
                    )
                    await self._summarize_messages(
                        llm_client, guild_id, msg_text, messages
                    )
                else:
                    logger.warning("LLM 미사용 — 요약 없이 메시지 삭제")

                msg_ids = [m["id"] for m in messages]
                placeholders = ",".join("?" * len(msg_ids))
                await self._db.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    msg_ids,
                )
                await self._db.commit()

                stats["deleted"] += msg_count
                logger.info(
                    f"서버 {guild_id} 정리 완료: {msg_count}개 메시지 삭제"
                )

        # 만료된 conversations 레코드도 삭제
        await self._db.execute(
            "DELETE FROM conversations WHERE created_at < ?",
            (cutoff,),
        )
        await self._db.commit()

        return stats

    async def _extract_important_events_from_messages(
        self,
        llm_client,
        guild_id: int,
        msg_text: str,
        messages: list[dict],
    ):
        """만료 메시지에서 중요 이벤트를 추출합니다 (서버 단위)."""
        unique_users = set(m["user_name"] for m in messages)
        participant_hint = (
            f"\n\n[참고: 참여 유저 수: {len(unique_users)}명 "
            f"({', '.join(list(unique_users)[:10])})]"
        )

        try:
            result = await llm_client.chat(
                prompt=msg_text + participant_hint,
                system_prompt=(
                    "아래 Discord 서버의 대화 기록을 분석하여 중요한 사건이나 이벤트를 추출하세요.\n\n"
                    "중요한 사건의 기준:\n"
                    "- 서버 규칙 변경, 장애 발생, 중요 결정 등 내용적으로 중요한 것\n"
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
                        channel_id=0,
                        event_title=event["event_title"],
                        description=event.get("description", ""),
                        participants=event.get("participants", []),
                        importance=event.get("importance", "high"),
                        occurred_at=event.get("occurred_at"),
                    )

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"중요 이벤트 추출 실패: {e}")

    async def _summarize_messages(
        self,
        llm_client,
        guild_id: int,
        msg_text: str,
        messages: list[dict],
    ):
        """만료 메시지를 서버 단위로 주제별 요약합니다."""
        try:
            result = await llm_client.chat(
                prompt=msg_text,
                system_prompt=(
                    "아래 Discord 서버의 여러 채널 대화 기록을 이벤트/주제 단위로 간결하게 요약하세요.\n"
                    "핵심 내용만 2~3문장으로 축약하세요.\n"
                    "불필요한 잡담은 제외하세요.\n"
                    "반드시 한국어로 답하세요."
                ),
            )

            if not result.available or not result.content:
                return

            period_start = messages[0]["created_at"]
            period_end = messages[-1]["created_at"]

            await self.save_summary(
                guild_id=guild_id,
                channel_id=0,
                summary=result.content.strip(),
                period_start=period_start,
                period_end=period_end,
                message_count=len(messages),
            )

        except Exception as e:
            logger.warning(f"메시지 요약 실패: {e}")

    # ─── 관련성 필터 ────────────────────────────────────────

    # 조사·어미·감탄사 등 의미 없는 토큰 패턴
    _STRIP_TAIL = re.compile(r'[이가을를은는의에도와과고~?!.,ㅋㅎㅠㅜ]+$')

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """텍스트에서 의미 있는 키워드(2자 이상)를 추출합니다."""
        keywords = []
        for token in text.split():
            token = MemoryManager._STRIP_TAIL.sub('', token)
            if len(token) >= 2:
                keywords.append(token)
        return keywords

    @staticmethod
    def _is_relevant(item: str, keywords: list[str]) -> bool:
        """item이 키워드 목록과 관련 있는지 판단합니다.

        키워드가 없으면 항상 관련 있음(True) 반환.
        """
        if not keywords:
            return True
        return any(kw in item for kw in keywords)

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
        for table in ("messages", "conversations", "summaries", "important_events", "user_facts"):
            async with self._db.execute(
                f"SELECT COUNT(*) FROM {table}"
            ) as cursor:
                row = await cursor.fetchone()
                stats[table] = row[0]
        return stats
