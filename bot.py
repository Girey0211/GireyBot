"""
Discord Support Agent — 봇 엔트리포인트

최소 실행 코드: Gateway 연결, on_ready, on_message 로그, /ping 명령어
"""

import os
import logging

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ─── 환경변수 로드 ───────────────────────────────────────────
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")

# Discord Bot Token 형식 기본 검증 (Base64, 점 구분 3파트)
if "." not in DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN 형식이 올바르지 않습니다.\n"
        "Discord Bot Token은 'MTAxNjU5ODk4OTE0MTYyMDgzNA.G1a2b3.XYZ...' 같은 형식입니다.\n"
        "Discord Developer Portal > Application > Bot > Reset Token 에서 복사하세요.\n"
        "※ Application ID, Client Secret, OAuth2 Secret 과 혼동하지 마세요."
    )

# ─── 로깅 설정 ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("girey-bot")

# ─── 봇 설정 ─────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # MESSAGE_CONTENT Privileged Intent

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    description="기리봇 — Discord 서버 운영 지원 봇",
)


# ─── 이벤트 핸들러 ───────────────────────────────────────────
@bot.event
async def on_ready():
    """봇 로그인 완료 시 호출"""
    logger.info(f"봇 로그인 성공: {bot.user} (ID: {bot.user.id})")
    logger.info(f"연결된 서버 수: {len(bot.guilds)}")
    for guild in bot.guilds:
        logger.info(f"  - {guild.name} (ID: {guild.id})")

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

    # 디버그 로그 (향후 자동 감지 시스템으로 대체 예정)
    logger.debug(
        f"[{message.guild.name}/#{message.channel.name}] "
        f"{message.author.display_name}: {message.content[:100]}"
    )

    # 접두사 명령어 처리 (향후 필요 시)
    await bot.process_commands(message)


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
