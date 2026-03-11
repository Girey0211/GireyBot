"""
Discord Support Agent — 봇 엔트리포인트

Gateway 연결, 이벤트 핸들링, 호출 감지 시스템 통합
"""

import logging

import discord
from discord.ext import commands

from core.config_loader import load_default_config, get_bot_names, get_discord_token
from core.call_detector import CallDetector, CallDetectionResult
from core.llm_client import create_llm_client

# ─── 로깅 설정 ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("girey-bot")

# ─── 설정 로드 ───────────────────────────────────────────────
# config/default.yaml + config/secrets.yaml 병합
config = load_default_config()

# Discord 토큰 (secrets.yaml → discord.token)
DISCORD_TOKEN = get_discord_token(config)

# ─── LLM 클라이언트 초기화 ────────────────────────────────────
llm_client = create_llm_client(config)
logger.info(f"LLM 프로바이더: {llm_client.provider_name}")

# ─── 봇 설정 ─────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # MESSAGE_CONTENT Privileged Intent

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    description="기리봇 — Discord 서버 운영 지원 봇",
)

# CallDetector는 on_ready에서 bot.user.id를 알 수 있을 때 초기화
call_detector: CallDetector | None = None


# ─── 이벤트 핸들러 ───────────────────────────────────────────
@bot.event
async def on_ready():
    """봇 로그인 완료 시 호출"""
    global call_detector

    logger.info(f"봇 로그인 성공: {bot.user} (ID: {bot.user.id})")
    logger.info(f"연결된 서버 수: {len(bot.guilds)}")
    for guild in bot.guilds:
        logger.info(f"  - {guild.name} (ID: {guild.id})")

    # ── 호출 감지 시스템 초기화 ──
    auto_detect_config = config.get("auto_detect", {})
    bot_names = get_bot_names(config)

    # config의 auto_detect.keywords를 추가 키워드로 사용
    extra_keywords = auto_detect_config.get("keywords", [])

    call_detector = CallDetector(
        bot_id=bot.user.id,
        bot_names=bot_names,
        keywords=extra_keywords,
        llm_client=llm_client,
        auto_detect_enabled=auto_detect_config.get("enabled", True),
        auto_detect_channels=auto_detect_config.get("channels", []),
    )
    logger.info("호출 감지 시스템 초기화 완료")

    # 슬래시 명령어 동기화
    try:
        synced = await bot.tree.sync()
        logger.info(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        logger.error(f"슬래시 명령어 동기화 실패: {e}")


@bot.event
async def on_message(message: discord.Message):
    """서버 내 메시지 수신 시 호출"""
    # 봇 자신의 메시지는 무시
    if message.author == bot.user:
        return

    # DM 메시지는 무시 (서버 전용 봇)
    if message.guild is None:
        return

    # 디버그 로그
    logger.debug(
        f"[{message.guild.name}/#{message.channel.name}] "
        f"{message.author.display_name}: {message.content[:100]}"
    )

    # ── 호출 감지 ──
    if call_detector:
        result = await call_detector.detect(message)

        if result.detected:
            await _handle_detected_call(message, result)

    # 접두사 명령어 처리 (향후 필요 시)
    await bot.process_commands(message)


async def _handle_detected_call(
    message: discord.Message,
    result: CallDetectionResult,
):
    """
    호출이 감지되었을 때의 응답 처리.

    LLM이 준비되지 않은 상태에서는 안내 메시지를 표시합니다.
    """
    # LLM 응답이 있고, 사용 불가능 상태인 경우
    llm_unavailable = (
        result.llm_response is not None and not result.llm_response.available
    )

    if llm_unavailable:
        # LLM 미셋업 상태 — 감지 결과와 함께 안내 메시지
        embed = discord.Embed(
            title="🤖 기리봇 — 호출 감지됨",
            description="호출이 감지되었지만, AI 엔진이 아직 준비되지 않았습니다.",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="감지 방식",
            value=_trigger_type_label(result.trigger_type),
            inline=True,
        )
        if result.matched_keyword:
            embed.add_field(
                name="매칭 키워드",
                value=f"`{result.matched_keyword}`",
                inline=True,
            )
        embed.add_field(
            name="확신도",
            value=f"{result.confidence:.0%}",
            inline=True,
        )
        embed.set_footer(
            text=f"⚠️ LLM 설정 후 AI 응답이 활성화됩니다. ({llm_client.provider_name})"
        )

        await message.reply(embed=embed, mention_author=False)
    else:
        # LLM 사용 가능 — 실제 응답 생성
        async with message.channel.typing():
            llm_response = await llm_client.chat(
                prompt=message.content,
                system_prompt=(
                    "당신은 Discord 서버 지원 봇 '기리봇'입니다. "
                    "사용자의 요청에 친절하고 간결하게 답변하세요. "
                    "Discord 메시지이므로 마크다운 형식을 활용하세요."
                ),
            )

        if llm_response.available and llm_response.content:
            # 응답이 Discord 글자 제한(2000자)을 넘으면 잘라서 전송
            response_config = config.get("response", {})
            max_len = response_config.get("max_length", 2000)
            content = llm_response.content

            if len(content) > max_len:
                content = content[: max_len - 20] + "\n\n…(응답이 잘렸습니다)"

            await message.reply(content, mention_author=False)
        else:
            # LLM 호출 실패
            reason = llm_response.reason or "알 수 없는 오류"
            embed = discord.Embed(
                title="🤖 기리봇 — 오류",
                description=f"응답 생성에 실패했습니다.\n`{reason}`",
                color=discord.Color.red(),
            )
            await message.reply(embed=embed, mention_author=False)


def _trigger_type_label(trigger_type: str | None) -> str:
    """트리거 타입을 사람이 읽을 수 있는 레이블로 변환"""
    labels = {
        "mention": "📢 멘션 (@봇)",
        "keyword": "🔑 키워드 감지",
        "auto_detect": "🔍 자동 감지 (AI 분석)",
    }
    return labels.get(trigger_type, "❓ 알 수 없음")


# ─── 슬래시 명령어 ───────────────────────────────────────────
@bot.tree.command(name="ping", description="봇 생존 확인")
async def ping(interaction: discord.Interaction):
    """봇이 살아있는지 확인하는 테스트 명령어"""
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(
        f"🏓 Pong! (지연: {latency_ms}ms)",
        ephemeral=True,
    )


# ─── 실행 ────────────────────────────────────────────────────
def main():
    """봇 실행 엔트리포인트 (uv run server)"""
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
