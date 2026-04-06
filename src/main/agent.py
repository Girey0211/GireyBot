"""
기리봇 메인 에이전트 클래스

봇의 코어 로직(상태 관리, 이벤트 핸들링, 호출 감지, 메모리 파이프라인)을 캡슐화합니다.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands, tasks

from src.shared.config import load_default_config, get_bot_names, get_discord_token
from src.main.detection import CallDetector, CallDetectionResult
from src.main.feedback import FeedbackManager, check_content
from src.shared.llm import create_llm_clients, LLMClients
from src.main.memory import MemoryManager
from src.main.skills.loader import SkillLoader
from src.main.skills.router import SkillRouter
from src.main.skills.executor import SkillExecutor
from src.main.skills.creator import SkillCreator, SkillCreationError
from src.main.rag.store import RAGStore
from src.main.rag.embedder import Embedder
from src.main.rag.ingest import Ingestor
from src.main.rag.retriever import Retriever

logger = logging.getLogger("girey-bot.agent")


class GireyBot(commands.Bot):
    """
    Discord Support Agent 코어 클래스

    서버 관리 및 운영 지원을 위한 LLM 및 메모리 시스템을 통합 관리합니다.
    """

    def __init__(self, *args, **kwargs):
        # 1. 설정 로드
        self.config = load_default_config()
        self.discord_token = get_discord_token(self.config)

        # Intents 설정
        intents = discord.Intents.default()
        intents.message_content = True  # 메시지 내용 접근 권한
        intents.reactions = True        # 리액션 이벤트 감지 권한
        intents.voice_states = True     # 음성 채널 상태 접근 권한

        kwargs["command_prefix"] = "!"
        kwargs["intents"] = intents
        if "description" not in kwargs:
            bot_name = self.config.get("bot", {}).get("name", "기리봇")
            kwargs["description"] = f"{bot_name} — Discord 서버 운영 지원 봇"

        super().__init__(*args, **kwargs)

        # 2. 봇 이름 (secrets.yaml → bot.name)
        self.bot_name: str = self.config.get("bot", {}).get("name", "기리봇")

        # 3. 컴포넌트 초기화
        self.llm: LLMClients = create_llm_clients(self.config)
        self.memory: MemoryManager = MemoryManager(self.config)
        self.call_detector: Optional[CallDetector] = None

        # 유저 피드백 관리자
        mem_config = self.config.get("memory", {})
        _db_path = str(
            Path(__file__).resolve().parent.parent.parent
            / mem_config.get("db_path", "data/memory.db")
        )
        self.feedback: FeedbackManager = FeedbackManager(_db_path)

        # 4. 스킬 시스템 초기화
        self.skill_loader: SkillLoader = SkillLoader(self.config)
        self.skill_router: Optional[SkillRouter] = None
        self.skill_executor: SkillExecutor = SkillExecutor(self.llm.analysis)

        # 5. RAG 시스템 초기화
        self.rag_store: RAGStore = RAGStore(self.config)
        self.rag_embedder: Embedder = Embedder(self.config)
        self.rag_ingestor: Ingestor = Ingestor(self.rag_store, self.rag_embedder, self.config)
        self.rag_retriever: Retriever = Retriever(self.rag_store, self.rag_embedder, self.config)

        logger.info(
            f"LLM 초기화 완료 — "
            f"simple={self.llm.simple.provider_name}/{self.llm.simple.model}, "
            f"roleplay={self.llm.roleplay.provider_name}/{self.llm.roleplay.model}, "
            f"analysis={self.llm.analysis.provider_name}/{self.llm.analysis.model}"
        )

    async def setup_hook(self):
        """봇이 시작될 때 1회 실행되는 초기화 훅"""

        # 1. 봇 정보 로드 후 CallDetector 셋업 (비동기 대기)
        self.loop.create_task(self._initialize_call_detector())

        # 2. 메모리 시스템 구동
        await self.memory.initialize()
        logger.info("메모리 시스템 초기화 완료")

        # 2-1. RAG 시스템 구동
        await self.rag_store.initialize()
        if self.rag_store.is_available:
            stats = await self.rag_ingestor.ingest_from_db(self.memory)
            logger.info(f"RAG DB 인덱싱 완료: {stats}")
        else:
            logger.warning("RAG 비활성화 — ChromaDB 연결 실패")

        # 3. 스킬 시스템 로드
        skills = self.skill_loader.load_all()
        if skills:
            self.skill_router = SkillRouter(skills, self.llm.simple)
            logger.info(f"스킬 시스템 초기화 완료: {len(skills)}개 로드")

        # 4. Cog 로드
        initial_extensions = [
            "src.main.cogs.general",
            "src.main.cogs.skill_commands",
            "src.main.cogs.voice",
            "src.main.cogs.rag",
        ]

        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
                logger.info(f"Ext 로드 성공: {extension}")
            except Exception as e:
                logger.error(f"Ext 로드 실패 — {extension}: {e}")

        # 5. 슬래시 명령어 동기화
        try:
            synced = await self.tree.sync()
            logger.info(f"슬래시 명령어 {len(synced)}개 동기화 완료")
        except Exception as e:
            logger.error(f"슬래시 명령어 동기화 실패: {e}")

        # 6. 메모리 정리 태스크 스케줄링
        cleanup_hours = self.config.get("memory", {}).get("cleanup_interval_hours", 24)
        self.memory_cleanup_task.change_interval(hours=cleanup_hours)
        if not self.memory_cleanup_task.is_running():
            self.memory_cleanup_task.start()
            logger.info("메모리 정리 태스크 스케줄링 시작")

    async def on_ready(self):
        """웹소켓이 Discord Gateway에 성공적으로 연결되었을 때 호출"""
        logger.info(f"봇 로그인 성공: {self.user} (ID: {self.user.id})")
        logger.info(f"연결된 서버 수: {len(self.guilds)}")
        for guild in self.guilds:
            logger.info(f"  - {guild.name} (ID: {guild.id})")

    async def on_message(self, message: discord.Message):
        """서버 내 메시지 수신 시 호출"""
        # 봇 자신의 메시지는 무시
        if message.author == self.user:
            return

        # DM 메시지는 무시 (서버 전용 봇)
        if message.guild is None:
            return

        logger.debug(
            f"[{message.guild.name}/#{message.channel.name}] "
            f"{message.author.display_name}: {message.content[:100]}"
        )

        # ── 모든 메시지 저장 ──
        if message.content.strip():
            await self.memory.save_message(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                user_id=message.author.id,
                user_name=message.author.display_name,
                content=message.content,
                channel_name=getattr(message.channel, "name", ""),
            )

        # ── 감지 및 반응 파이프라인 ──
        if self.call_detector:
            result = await self.call_detector.detect(message)
            if result.detected:
                await self._handle_detected_call(message, result)

        # 접두사 명령어 처리 프레임워크 유지 (필요성 감안)
        await self.process_commands(message)

    async def _handle_detected_call(self, message: discord.Message, result: CallDetectionResult):
        """호출이 감지되었을 때 관리 의도 확인 → 스킬 라우팅 → LLM 응답 파이프라인을 처리합니다."""
        llm_unavailable = result.llm_response is not None and not result.llm_response.available

        if llm_unavailable:
            await self._send_unavailable_message(message, result)
            return

        # ── 피드백: 콘텐츠 검사 ──
        if self.config.get("feedback", {}).get("enabled", True):
            check = await check_content(self.llm.simple, message.content)
            if check.violation:
                new_score = await self.feedback.add_violation(
                    user_id=message.author.id,
                    guild_id=message.guild.id,
                    violation_type=check.violation_type,
                    score_delta=check.score_delta,
                )
                logger.info(
                    f"콘텐츠 위반 처리 — user={message.author.id} "
                    f"type={check.violation_type} score={new_score}"
                )
                await self._send_violation_reply(message, check.violation_type)
                return

        thinking_msg = await message.channel.send("💭 생각중입니다...")
        try:
            # ── 0단계: 스킬 관리 의도 먼저 확인 ──
            management_intent = await self._detect_management_intent(message.content)
            if management_intent:
                await thinking_msg.delete()
                thinking_msg = None
                await self._handle_skill_management(message, management_intent)
                return

            # ── 스킬 라우팅 시도 ──
            if self.skill_router:
                match_result = await self.skill_router.route(message.content)

                if match_result.needs_clarification:
                    # 신뢰도 60 이하 → 사용자에게 재확인
                    await thinking_msg.delete()
                    thinking_msg = None
                    await self._handle_skill_clarification(message, match_result)
                    return

                if match_result.skill is not None:
                    # 스킬 매칭 확정 → 스킬 실행
                    await thinking_msg.delete()
                    thinking_msg = None
                    await self._execute_skill(message, match_result.skill)
                    return

            # ── 스킬 매칭 없음 → 기존 자유 대화 ──
            await self._handle_free_chat(message, thinking_msg)
            thinking_msg = None
        finally:
            if thinking_msg:
                try:
                    await thinking_msg.delete()
                except Exception:
                    pass

    async def _handle_skill_clarification(self, message: discord.Message, match_result):
        """신뢰도 미달 시 사용자에게 스킬 선택을 요청하고, 선택 결과에 따라 실행합니다."""
        view = await SkillExecutor.send_clarification(message, match_result)
        if view is None:
            # clarification UI 전송 실패 → 자유 대화로 fallback
            await self._handle_free_chat(message)
            return

        # 사용자가 버튼을 누르거나 타임아웃될 때까지 대기
        timed_out = await view.wait()

        if timed_out or view.selected_skill is None:
            # 타임아웃 또는 취소 → 자유 대화로 fallback
            await self._handle_free_chat(message)
            return

        # 선택된 스킬 실행
        await self._execute_skill(message, view.selected_skill)

    async def _execute_skill(self, message: discord.Message, skill):
        """스킬을 실행하고 결과를 전송합니다."""
        async with message.channel.typing():
            context_mode = skill.metadata.get("context_mode")

            if context_mode == "session":
                # 대화 세션 기반 컨텍스트 (요약 스킬 등)
                gap = skill.metadata.get("session_gap_minutes", 10)
                session = await self.memory.get_conversation_session(
                    channel_id=message.channel.id,
                    gap_minutes=gap,
                )
                if session:
                    session_lines = "\n".join(
                        f"[{s.created_at}] {s.user_name}: {s.user_message}\n  봇: {s.bot_response}"
                        for s in session
                    )
                    context = (
                        f"## 대화 세션 ({len(session)}개 메시지)\n"
                        f"기간: {session[0].created_at} ~ {session[-1].created_at}\n\n"
                        f"{session_lines}"
                    )
                else:
                    context = "## 대화 세션\n(이 채널에 최근 대화 기록이 없습니다.)"
            else:
                context, _ = await self.memory.build_context(
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    user_id=message.author.id,
                    user_message=message.content,
                    retriever=self.rag_retriever,
                )

            result_text = await self.skill_executor.execute(
                skill=skill,
                user_message=message.content,
                context=context,
            )

        await SkillExecutor.send_skill_result(message, skill, result_text)

        # 대화 기록 저장
        await self._record_conversation(message, result_text)
        self._trigger_fact_extraction(message, result_text)

        if self.call_detector:
            self.call_detector.register_active_conversation(
                channel_id=message.channel.id,
                user_message=message.content,
                bot_response=result_text,
            )

    # ─── 스킬 관리 의도 감지 및 처리 ──────────────

    async def _detect_management_intent(self, content: str) -> str | None:
        """
        메시지에서 스킬 관리 의도를 감지합니다.

        Returns:
            "create" | "delete" | "edit" | "list" | "reload" | None
        """
        # ── 키워드 기반 판별 ──
        content_lower = content.lower()
        _KW_CREATE = ("스킬 만들", "스킬 생성", "스킬 추가", "새 스킬", "새로운 스킬")
        _KW_DELETE = ("스킬 삭제", "스킬 지워", "스킬 제거")
        _KW_EDIT   = ("스킬 수정", "스킬 편집", "스킬 변경")
        _KW_LIST   = ("스킬 목록", "스킬 리스트", "어떤 스킬", "스킬 보여")
        _KW_RELOAD = ("스킬 리로드", "스킬 새로고침", "스킬 reload")

        for kw in _KW_CREATE:
            if kw in content_lower:
                return "create"
        for kw in _KW_DELETE:
            if kw in content_lower:
                return "delete"
        for kw in _KW_EDIT:
            if kw in content_lower:
                return "edit"
        for kw in _KW_LIST:
            if kw in content_lower:
                return "list"
        for kw in _KW_RELOAD:
            if kw in content_lower:
                return "reload"

        return None

    async def _handle_skill_management(
        self,
        message: discord.Message,
        intent: str,
    ) -> None:
        """관리 의도에 따라 스킬 생성을 처리하거나 슬래시 명령어를 안내합니다."""

        if intent == "create":
            await self._handle_skill_create(message)
            return

        # 삭제/수정/목록/리로드 → 슬래시 명령어 안내
        _GUIDE: dict[str, tuple[str, str]] = {
            "delete": ("/skills-delete", "삭제할 스킬 이름을 선택하여 삭제합니다."),
            "edit":   ("/skills-edit",   "편집할 스킬을 선택하고 본문을 수정합니다."),
            "list":   ("/skills",        "사용 가능한 스킬 목록을 확인합니다."),
            "reload": ("/skills-reload", "스킬을 다시 로드합니다. (관리자 전용)"),
        }
        cmd, desc = _GUIDE.get(intent, ("/skills", "스킬 목록을 확인하세요."))

        embed = discord.Embed(
            title="🛠️ 스킬 관리",
            description=f"`{cmd}` 명령어를 사용하세요.\n{desc}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="슬래시 명령어로 안전하게 스킬을 관리할 수 있습니다.")
        await message.reply(embed=embed, mention_author=False)

    async def _handle_skill_create(self, message: discord.Message) -> None:
        """SkillCreator를 사용하여 스킬을 생성하고 결과를 전송합니다."""
        async with message.channel.typing():
            creator = SkillCreator(self.llm.analysis)
            try:
                draft = await creator.collect_info(message.content)
            except SkillCreationError as e:
                await message.reply(
                    f"⚠️ 스킬 정보 추출 실패: {e}",
                    mention_author=False,
                )
                return

            try:
                body = await creator.generate_body(draft)
                skill_path = creator.write_skill(draft, body)
            except SkillCreationError as e:
                await message.reply(
                    f"⚠️ 스킬 생성 실패: {e}",
                    mention_author=False,
                )
                return

        # 스킬 리로드
        skills = self.skill_loader.load_all()
        if skills and self.skill_router:
            self.skill_router.update_skills(skills)
        elif skills:
            self.skill_router = SkillRouter(skills, self.llm.simple)

        logger.info(f"스킬 생성 완료: {draft.name} → {skill_path}")

        # 결과 Embed
        triggers_str = ", ".join(draft.triggers[:6]) if draft.triggers else "(없음)"
        embed = discord.Embed(
            title=f"✅ 스킬 `{draft.name}` 생성 완료",
            description=draft.description,
            color=discord.Color.green(),
        )
        embed.add_field(name="트리거", value=triggers_str, inline=False)
        if draft.executor:
            embed.add_field(name="실행기", value=draft.executor, inline=True)
        if draft.credentials:
            embed.add_field(name="credentials", value=draft.credentials, inline=True)
        if draft.notes:
            embed.add_field(name="비고", value=draft.notes[:500], inline=False)
        embed.add_field(
            name="파일",
            value=f"`src/shared/skills/{draft.name}/SKILL.md`",
            inline=False,
        )

        is_active = self.skill_loader.get_skill(draft.name) is not None
        needs_setup = draft.name in self.skill_loader.unconfigured_skills
        if is_active:
            embed.set_footer(text=f"/skill {draft.name} 으로 실행하세요.")
        elif needs_setup:
            embed.set_footer(text=f"⚠️ 접속 정보 설정 필요: /skills-setup {draft.name}")
        else:
            embed.set_footer(text="/skills-edit 으로 본문을 직접 수정할 수 있습니다.")

        await message.reply(embed=embed, mention_author=False)

    async def _handle_free_chat(
        self,
        message: discord.Message,
        thinking_msg: discord.Message | None = None,
    ):
        """스킬 매칭이 없을 때 스트리밍으로 자유 대화 응답을 처리합니다."""
        # 유저 모드 확인 (refuse면 응답 거부)
        user_mode = await self.feedback.get_response_mode(message.author.id)
        if user_mode == "refuse":
            if thinking_msg:
                await thinking_msg.delete()
            await self._send_refuse_reply(message)
            return

        # 봇 이름/별명을 제거한 순수 질문을 RAG 쿼리에 사용
        rag_query = re.sub(
            r"(?i)\b" + re.escape(self.bot_name) + r"\b",
            "",
            message.content,
        ).strip(" ,!?")

        context, rag_context = await self.memory.build_context(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            user_id=message.author.id,
            user_message=message.content,
            retriever=self.rag_retriever,
            rag_query=rag_query or message.content,
        )

        persona = self.memory.load_persona()
        system_prompt = persona or (
            f"당신은 Discord 서버 지원 봇 '{self.bot_name}'입니다. "
            "사용자의 요청에 친절하고 간결하게 답변하세요. "
        )
        if user_mode != "normal":
            system_prompt = f"[USER_MODE: {user_mode}]\n\n{system_prompt}"

        # RAG 결과는 user prompt 앞에 직접 주입 — system보다 훨씬 높은 우선순위
        if rag_context:
            prompt = f"{rag_context}\n\n---\n위 정보를 바탕으로 다음 질문에 답해줘:\n{message.content}"
        else:
            prompt = message.content

        response_config = self.config.get("response", {})
        max_len = response_config.get("max_length", 2000)

        accumulated = ""
        last_edit_len = 0
        edit_threshold = 50  # 50자마다 메시지 편집
        error_occurred = False

        try:
            async for chunk in self.llm.roleplay.chat_stream(
                prompt=prompt,
                system_prompt=system_prompt,
                context=context if context else None,
            ):
                accumulated += chunk
                # Discord 레이트 리밋 고려: 50자 누적마다 편집
                if thinking_msg and len(accumulated) - last_edit_len >= edit_threshold:
                    display = accumulated if len(accumulated) <= max_len else accumulated[: max_len - 20] + "…"
                    try:
                        await thinking_msg.edit(content=display)
                        last_edit_len = len(accumulated)
                    except discord.HTTPException:
                        pass
        except Exception as e:
            logger.error(f"[FreeChat] 스트리밍 실패: {e}")
            error_occurred = True

        if error_occurred or not accumulated:
            reason = "스트리밍 응답 생성에 실패했습니다." if error_occurred else "알 수 없는 오류"
            embed = discord.Embed(
                title=f"🤖 {self.bot_name} — 오류",
                description=f"응답 생성에 실패했습니다.\n`{reason}`",
                color=discord.Color.red(),
            )
            if thinking_msg:
                try:
                    await thinking_msg.delete()
                except discord.HTTPException:
                    pass
            await message.reply(embed=embed, mention_author=False)
            return

        # 길이 초과 시 자르기
        if len(accumulated) > max_len:
            accumulated = accumulated[: max_len - 20] + "\n\n…(응답이 잘렸습니다)"

        # 최종 편집 또는 새 답장
        if thinking_msg:
            try:
                await thinking_msg.edit(content=accumulated)
            except discord.HTTPException:
                await message.reply(accumulated, mention_author=False)
        else:
            await message.reply(accumulated, mention_author=False)

        await self._record_conversation(message, accumulated)
        self._trigger_fact_extraction(message, accumulated)

        if self.call_detector:
            self.call_detector.register_active_conversation(
                channel_id=message.channel.id,
                user_message=message.content,
                bot_response=accumulated,
            )

    async def _send_llm_reply(self, message: discord.Message, content: str):
        """메시지 길이 제한(Discord 2000자)을 고려하여 응답 전송"""
        response_config = self.config.get("response", {})
        max_len = response_config.get("max_length", 2000)

        if len(content) > max_len:
            content = content[: max_len - 20] + "\n\n…(응답이 잘렸습니다)"

        await message.reply(content, mention_author=False)

    async def _record_conversation(self, message: discord.Message, reply_content: str):
        """대화 기록(메시지 본문 및 관련 메타데이터)을 데이터베이스에 적재"""
        reaction_count = sum(r.count for r in message.reactions) if message.reactions else 0
        channel_name = getattr(message.channel, "name", "")
        await self.memory.save_conversation(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            user_id=message.author.id,
            user_name=message.author.display_name,
            user_message=message.content,
            bot_response=reply_content,
            channel_name=channel_name,
            reaction_count=reaction_count,
        )

    def _trigger_fact_extraction(self, message: discord.Message, reply_content: str):
        """유저 팩트 추출 과정을 비동기 배경 작업으로 전송"""
        asyncio.create_task(
            self.memory.extract_and_save_facts(
                llm_client=self.llm.simple,
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_message=message.content,
                bot_response=reply_content,
                source_message_id=message.id,
            )
        )

    async def _send_violation_reply(self, message: discord.Message, violation_type: str):
        """콘텐츠 위반 감지 시 거절 메시지를 전송합니다."""
        _VIOLATION_LABELS = {
            "obscene":      "음란·성적 내용",
            "political":    "정치적 편향 유도",
            "unreasonable": "무리한·악의적 요청",
        }
        label = _VIOLATION_LABELS.get(violation_type, "부적절한 내용")
        tag = f"[CONTENT_VIOLATION: {violation_type}]"

        persona = self.memory.load_persona()
        system_prompt = persona or f"당신은 Discord 봇 '{self.bot_name}'입니다."
        system_prompt = f"{tag}\n\n{system_prompt}"

        try:
            llm_response = await self.llm.roleplay.chat(
                prompt=message.content,
                system_prompt=system_prompt,
            )
            if llm_response.available and llm_response.content:
                await message.reply(llm_response.content, mention_author=False)
                return
        except Exception:
            pass

        # LLM 실패 시 기본 거절 메시지
        embed = discord.Embed(
            title="⚠️ 응답 거절",
            description=f"이 질문은 **{label}**으로 판단되어 응답이 거절되었습니다.",
            color=discord.Color.red(),
        )
        await message.reply(embed=embed, mention_author=False)

    async def _send_refuse_reply(self, message: discord.Message):
        """누적 점수 초과 유저에게 응답 거부 메시지를 전송합니다."""
        persona = self.memory.load_persona()
        system_prompt = persona or f"당신은 Discord 봇 '{self.bot_name}'입니다."
        system_prompt = f"[USER_MODE: refuse]\n\n{system_prompt}"

        try:
            llm_response = await self.llm.roleplay.chat(
                prompt=message.content,
                system_prompt=system_prompt,
            )
            if llm_response.available and llm_response.content:
                await message.reply(llm_response.content, mention_author=False)
                return
        except Exception:
            pass

        embed = discord.Embed(
            title="🚫 응답 거부",
            description="누적된 위반으로 인해 이 사용자에게는 응답하지 않습니다.",
            color=discord.Color.dark_red(),
        )
        await message.reply(embed=embed, mention_author=False)

    async def _send_unavailable_message(self, message: discord.Message, result: CallDetectionResult):
        """LLM 연동이 불가능할 경우 보여주는 오류/안내 임베드 포맷 전송"""
        embed = discord.Embed(
            title=f"🤖 {self.bot_name} — 호출 감지됨",
            description="호출이 감지되었지만, AI 엔진이 아직 준비되지 않았습니다.",
            color=discord.Color.orange(),
        )

        # 감지 방식 구문 분석
        trigger_labels = {
            "mention": "📢 멘션 (@봇)",
            "keyword": "🔑 키워드 감지",
            "auto_detect": "🔍 자동 감지 (AI 분석)",
            "continuation": "💬 대화 연속",
        }
        trigger_label = trigger_labels.get(result.trigger_type, "❓ 알 수 없음")

        embed.add_field(name="감지 방식", value=trigger_label, inline=True)
        if result.matched_keyword:
            embed.add_field(name="매칭 키워드", value=f"`{result.matched_keyword}`", inline=True)
        embed.add_field(name="확신도", value=f"{result.confidence:.0%}", inline=True)
        embed.set_footer(text=f"⚠️ LLM 설정 후 AI 응답이 활성화됩니다. ({self.llm.roleplay.provider_name})")

        await message.reply(embed=embed, mention_author=False)


    # ─── 봇 컴포넌트 초기화 ──────────────
    async def _initialize_call_detector(self):
        """봇이 준비될 때까지 대기했다가 CallDetector를 초기화합니다."""
        await self.wait_until_ready()

        if not self.call_detector and self.user:
            auto_detect_config = self.config.get("auto_detect", {})
            bot_names = get_bot_names(self.config)
            extra_keywords = auto_detect_config.get("keywords", [])

            self.call_detector = CallDetector(
                bot_id=self.user.id,
                bot_names=bot_names,
                keywords=extra_keywords,
                llm_client=self.llm.simple,
                auto_detect_enabled=auto_detect_config.get("enabled", True),
                auto_detect_channels=auto_detect_config.get("channels", []),
            )
            logger.info("호출 감지 시스템 초기화 완료")


    # ─── 타이머 태스크 (배경 루프) ──────────────


    @tasks.loop(hours=24)
    async def memory_cleanup_task(self):
        """TTL 만료 레코드에 대한 요약/정리를 담당하는 스케줄러.
        interval은 setup_hook() 에서 동적으로 설정할 수 있음.
        """
        logger.info("메모리 정리 주기 프로세스 시작...")
        try:
            stats = await self.memory.cleanup(self.llm.analysis)
            logger.info(
                f"메모리 정리 완료: "
                f"채널 {stats.get('channels', 0)}개 분석, "
                f"대화 {stats.get('deleted', 0)}개 정리."
            )
        except Exception as e:
            logger.error(f"메모리 정리 중 오류: {e}")

    @memory_cleanup_task.before_loop
    async def before_memory_cleanup(self):
        await self.wait_until_ready()
