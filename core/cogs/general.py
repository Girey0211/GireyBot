"""
일반 명령어 Cog

봇의 상태 확인 및 메모리 통계 확인 등 기본 명령어를 제공합니다.
"""

import discord
from discord.ext import commands

class GeneralCog(commands.Cog, name="general"):
    """서버 운영 지원 봇의 기본 명령어 모음"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(name="ping", description="봇 생존 확인")
    async def ping(self, interaction: discord.Interaction):
        """봇이 살아있는지 확인하는 테스트 명령어"""
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"🏓 Pong! (지연: {latency_ms}ms)",
            ephemeral=True,
        )

    @discord.app_commands.command(name="memory", description="메모리 통계 확인")
    async def memory_stats(self, interaction: discord.Interaction):
        """메모리 시스템의 현재 통계를 조회합니다."""
        # bot 인스턴스에 memory 속성이 있다고 가정합니다 (agent.py에서 설정)
        if not hasattr(self.bot, 'memory') or not self.bot.memory:
            await interaction.response.send_message(
                "❌ 메모리 시스템이 초기화되지 않았습니다.",
                ephemeral=True
            )
            return

        memory = self.bot.memory
        stats = await memory.get_stats()
        
        embed = discord.Embed(
            title=f"🧠 {self.bot.bot_name} — 메모리 통계",
            color=discord.Color.blue(),
        )
        embed.add_field(name="💬 대화 기록", value=f"{stats.get('conversations', 0)}개", inline=True)
        embed.add_field(name="📝 요약", value=f"{stats.get('summaries', 0)}개", inline=True)
        embed.add_field(name="⚡ 중요 이벤트", value=f"{stats.get('important_events', 0)}개", inline=True)
        embed.add_field(name="🧩 유저 팩트", value=f"{stats.get('user_facts', 0)}개", inline=True)
        embed.set_footer(text=f"보존 기한: {memory.retention_days}일")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
