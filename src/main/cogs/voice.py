"""
음성 채널 관리 Cog

봇의 음성 채널 입장/퇴장 명령어를 제공합니다.
"""

import logging

import davey  # noqa: F401 — 음성 코덱 (빌드 시 포함 보장)
import nacl   # noqa: F401 — 음성 암호화 (빌드 시 포함 보장)

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("girey-bot.voice")


class VoiceCog(commands.Cog, name="voice"):
    """봇의 음성 채널 입장/퇴장 명령어"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="봇생성", description="봇을 음성 채널에 입장시킵니다")
    @app_commands.describe(channel="입장할 음성 채널 (미지정 시 현재 접속 중인 음성 채널)")
    async def voice_join(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | None = None,
    ):
        """봇을 지정한 음성 채널 또는 사용자의 현재 음성 채널에 입장시킵니다."""

        # 채널 미지정 → 사용자의 현재 음성 채널
        if channel is None:
            if interaction.user.voice and interaction.user.voice.channel:
                channel = interaction.user.voice.channel
            else:
                await interaction.response.send_message(
                    "❌ 입장할 채널을 지정하거나, 먼저 음성 채널에 접속해 주세요.",
                    ephemeral=True,
                )
                return

        # 이미 같은 채널에 접속 중인지 확인
        guild = interaction.guild
        if guild.voice_client and guild.voice_client.channel == channel:
            await interaction.response.send_message(
                f"이미 **{channel.name}** 채널에 접속 중입니다.",
                ephemeral=True,
            )
            return

        # 다른 채널에 접속 중이면 이동
        if guild.voice_client:
            await guild.voice_client.move_to(channel)
            await interaction.response.send_message(
                f"🔊 **{channel.name}** 채널로 이동했습니다.",
                ephemeral=True,
            )
            logger.info(f"[{guild.name}] 음성 채널 이동: {channel.name}")
            return

        # 새로 접속
        try:
            await channel.connect(self_deaf=True, reconnect=True)
        except discord.ClientException as e:
            await interaction.response.send_message(
                f"❌ 음성 채널 접속 실패: {e}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🔊 **{channel.name}** 채널에 입장했습니다.",
            ephemeral=True,
        )
        logger.info(f"[{guild.name}] 음성 채널 입장: {channel.name}")

    @app_commands.command(name="봇해제", description="봇을 음성 채널에서 퇴장시킵니다")
    async def voice_leave(self, interaction: discord.Interaction):
        """봇을 현재 접속 중인 음성 채널에서 퇴장시킵니다."""
        guild = interaction.guild

        if not guild.voice_client:
            await interaction.response.send_message(
                "❌ 현재 음성 채널에 접속되어 있지 않습니다.",
                ephemeral=True,
            )
            return

        channel_name = guild.voice_client.channel.name
        await guild.voice_client.disconnect()

        await interaction.response.send_message(
            f"🔇 **{channel_name}** 채널에서 퇴장했습니다.",
            ephemeral=True,
        )
        logger.info(f"[{guild.name}] 음성 채널 퇴장: {channel_name}")


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceCog(bot))
