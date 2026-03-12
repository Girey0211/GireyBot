"""
기리봇 메인 에이전트 클래스

봇의 코어 로직(상태 관리, 이벤트 핸들링, 호출 감지, 메모리 파이프라인)을 캡슐화합니다.
"""

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands, tasks

from core.config_loader import load_default_config, get_bot_names, get_discord_token
from core.detection import CallDetector, CallDetectionResult
from core.llm import create_llm_client, BaseLLMClient
from core.memory import MemoryManager

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

        kwargs["command_prefix"] = "!"
        kwargs["intents"] = intents
        if "description" not in kwargs:
            kwargs["description"] = "기리봇 — Discord 서버 운영 지원 봇"
            
        super().__init__(*args, **kwargs)

        # 2. 컴포넌트 초기화
        self.llm_client: BaseLLMClient = create_llm_client(self.config)
        self.memory: MemoryManager = MemoryManager(self.config)
        self.call_detector: Optional[CallDetector] = None

        logger.info(f"LLM 프로바이더 초기화됨: {self.llm_client.provider_name}")

    async def setup_hook(self):
        """봇이 시작될 때 1회 실행되는 초기화 훅"""
        
        # 1. 봇 정보 로드 후 CallDetector 셋업 (비동기 대기)
        self.loop.create_task(self._initialize_call_detector())
        
        # 2. 메모리 시스템 구동
        await self.memory.initialize()
        logger.info("메모리 시스템 초기화 완료")

        # 3. Cog 로드
        initial_extensions = [
            "core.cogs.general",
        ]
        
        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
                logger.info(f"Ext 로드 성공: {extension}")
            except Exception as e:
                logger.error(f"Ext 로드 실패 — {extension}: {e}")

        # 4. 슬래시 명령어 동기화
        try:
            synced = await self.tree.sync()
            logger.info(f"슬래시 명령어 {len(synced)}개 동기화 완료")
        except Exception as e:
            logger.error(f"슬래시 명령어 동기화 실패: {e}")
            
        # 5. 메모리 정리 태스크 스케줄링
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

        # ── 감지 및 반응 파이프라인 ──
        if self.call_detector:
            result = await self.call_detector.detect(message)
            if result.detected:
                await self._handle_detected_call(message, result)

        # 접두사 명령어 처리 프레임워크 유지 (필요성 감안)
        await self.process_commands(message)

    async def _handle_detected_call(self, message: discord.Message, result: CallDetectionResult):
        """호출이 감지되었을 때 LLM 및 메모리를 연동하여 응답을 처리합니다."""
        llm_unavailable = result.llm_response is not None and not result.llm_response.available

        if llm_unavailable:
            await self._send_unavailable_message(message, result)
            return

        # LLM 사용 가능 — 메모리 컨텍스트 통합 파이프라인 진행
        async with message.channel.typing():
            # 1. 정적/동적 메모리 컨텍스트 빌드
            context = await self.memory.build_context(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                user_id=message.author.id,
            )

            persona = self.memory.load_persona()
            system_prompt = persona or (
                "당신은 Discord 서버 지원 봇 '기리봇'입니다. "
                "사용자의 요청에 친절하고 간결하게 답변하세요. "
            )

            # 2. LLM 응답 요청
            llm_response = await self.llm_client.chat(
                prompt=message.content,
                system_prompt=system_prompt,
                context=context if context else None,
            )

        if llm_response.available and llm_response.content:
            await self._send_llm_reply(message, llm_response.content)

            # 3. 대화 정보 저장 (SQLite)
            await self._record_conversation(message, llm_response.content)

            # 4. 팩트 데이터 추출
            self._trigger_fact_extraction(message, llm_response.content)

            # 5. 활성 대화 등록 (채널 단위 연속 대화 추적)
            if self.call_detector:
                self.call_detector.register_active_conversation(
                    channel_id=message.channel.id,
                    user_message=message.content,
                    bot_response=llm_response.content,
                )
        else:
            reason = llm_response.reason or "알 수 없는 오류"
            embed = discord.Embed(
                title="🤖 기리봇 — 오류",
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
            title="🤖 기리봇 — 호출 감지됨",
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
