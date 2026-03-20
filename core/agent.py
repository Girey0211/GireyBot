"""
기리봇 메인 에이전트 클래스

봇의 코어 로직(상태 관리, 이벤트 핸들링, 호출 감지, 메모리 파이프라인)을 캡슐화합니다.
"""

import asyncio
import json
import logging
from typing import Optional

import discord
from discord.ext import commands, tasks

from core.config_loader import load_default_config, get_bot_names, get_discord_token
from core.detection import CallDetector, CallDetectionResult
from core.llm import create_llm_client, BaseLLMClient
from core.memory import MemoryManager
from core.skills.loader import SkillLoader
from core.skills.router import SkillRouter
from core.skills.executor import SkillExecutor
from core.skills.creator import SkillCreator, SkillCreationError

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
        self.llm_client: BaseLLMClient = create_llm_client(self.config)
        self.memory: MemoryManager = MemoryManager(self.config)
        self.call_detector: Optional[CallDetector] = None

        # 4. 스킬 시스템 초기화
        self.skill_loader: SkillLoader = SkillLoader(self.config)
        self.skill_router: Optional[SkillRouter] = None
        self.skill_executor: SkillExecutor = SkillExecutor(self.llm_client)

        logger.info(f"LLM 프로바이더 초기화됨: {self.llm_client.provider_name}")

    async def setup_hook(self):
        """봇이 시작될 때 1회 실행되는 초기화 훅"""
        
        # 1. 봇 정보 로드 후 CallDetector 셋업 (비동기 대기)
        self.loop.create_task(self._initialize_call_detector())
        
        # 2. 메모리 시스템 구동
        await self.memory.initialize()
        logger.info("메모리 시스템 초기화 완료")

        # 3. 스킬 시스템 로드
        skills = self.skill_loader.load_all()
        if skills:
            self.skill_router = SkillRouter(skills, self.llm_client)
            logger.info(f"스킬 시스템 초기화 완료: {len(skills)}개 로드")

        # 4. Cog 로드
        initial_extensions = [
            "core.cogs.general",
            "core.cogs.skill_commands",
            "core.cogs.voice",
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

        # ── 0단계: 스킬 관리 의도 먼저 확인 ──
        management_intent = await self._detect_management_intent(message.content)
        if management_intent:
            await self._handle_skill_management(message, management_intent)
            return

        # ── 스킬 라우팅 시도 ──
        if self.skill_router:
            match_result = await self.skill_router.route(message.content)

            if match_result.needs_clarification:
                # 신뢰도 60 이하 → 사용자에게 재확인
                await self._handle_skill_clarification(message, match_result)
                return

            if match_result.skill is not None:
                # 스킬 매칭 확정 → 스킬 실행
                await self._execute_skill(message, match_result.skill)
                return

        # ── 스킬 매칭 없음 → 기존 자유 대화 ──
        await self._handle_free_chat(message)

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
                context = await self.memory.build_context(
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    user_id=message.author.id,
                    user_message=message.content,
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
        from pathlib import Path
        import json, re

        # ── LLM 기반 판별 ──
        if self.llm_client and self.llm_client.is_available:
            prompt_path = Path(__file__).parent / "skills" / "prompts" / "management_intent.txt"
            try:
                sys_prompt = prompt_path.read_text(encoding="utf-8")
                response = await self.llm_client.chat(
                    prompt=content,
                    system_prompt=sys_prompt,
                )
                if response.available and response.content:
                    raw = response.content.strip()
                    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                    raw = re.sub(r"\n?```\s*$", "", raw)
                    m = re.search(r"\{.*\}", raw, re.DOTALL)
                    if m:
                        data = json.loads(m.group())
                        intent = data.get("intent", "null")
                        if intent and intent != "null":
                            logger.info(f"관리 의도 감지 (LLM): {intent}")
                            return intent
            except Exception as e:
                logger.warning(f"관리 의도 LLM 판별 실패, 키워드 폴백: {e}")

        # ── 키워드 기반 폴백 ──
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
            creator = SkillCreator(self.llm_client)
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
            self.skill_router = SkillRouter(skills, self.llm_client)

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
            value=f"`skills/{draft.name}/SKILL.md`",
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

    async def _handle_free_chat(self, message: discord.Message):
        """스킬 매칭이 없을 때 기존 자유 대화 로직을 수행합니다."""
        async with message.channel.typing():
            context = await self.memory.build_context(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                user_id=message.author.id,
                user_message=message.content,
            )

            persona = self.memory.load_persona()
            system_prompt = persona or (
                f"당신은 Discord 서버 지원 봇 '{self.bot_name}'입니다. "
                "사용자의 요청에 친절하고 간결하게 답변하세요. "
            )

            llm_response = await self.llm_client.chat(
                prompt=message.content,
                system_prompt=system_prompt,
                context=context if context else None,
            )

        if llm_response.available and llm_response.content:
            await self._send_llm_reply(message, llm_response.content)
            await self._record_conversation(message, llm_response.content)
            self._trigger_fact_extraction(message, llm_response.content)

            if self.call_detector:
                self.call_detector.register_active_conversation(
                    channel_id=message.channel.id,
                    user_message=message.content,
                    bot_response=llm_response.content,
                )
        else:
            reason = llm_response.reason or "알 수 없는 오류"
            embed = discord.Embed(
                title=f"🤖 {self.bot_name} — 오류",
                description=f"응답 생성에 실패했습니다.\n`{reason}`",
                color=discord.Color.red(),
            )
            await message.reply(embed=embed, mention_author=False)

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
                llm_client=self.llm_client,
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_message=message.content,
                bot_response=reply_content,
                source_message_id=message.id,
            )
        )

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
        embed.set_footer(text=f"⚠️ LLM 설정 후 AI 응답이 활성화됩니다. ({self.llm_client.provider_name})")

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
                llm_client=self.llm_client,
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
            stats = await self.memory.cleanup(self.llm_client)
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
