"""
사운드보드 Cog

음성 채널에서 등록된 사운드를 재생하는 기능을 제공합니다.
data/sounds/ 디렉터리에 오디오 파일(.mp3, .wav, .ogg)을 넣어 사용합니다.
"""

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("girey-bot.soundboard")

SOUNDS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sounds"
SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}


class SoundboardCog(commands.Cog, name="soundboard"):
    """음성 채널에서 사운드를 재생하는 사운드보드 기능"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)

    def _list_sounds(self) -> list[str]:
        """사용 가능한 사운드 목록을 반환합니다."""
        sounds = []
        for f in sorted(SOUNDS_DIR.iterdir()):
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
                sounds.append(f.stem)
        return sounds

    def _find_sound(self, name: str) -> Path | None:
        """이름으로 사운드 파일을 찾습니다."""
        for f in SOUNDS_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS and f.stem == name:
                return f
        return None

    async def _sound_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """사운드 이름 자동완성"""
        sounds = self._list_sounds()
        filtered = [s for s in sounds if current.lower() in s.lower()]
        return [app_commands.Choice(name=s, value=s) for s in filtered[:25]]

    @app_commands.command(name="사운드목록", description="사용 가능한 사운드 목록을 확인합니다")
    async def sound_list(self, interaction: discord.Interaction):
        """등록된 사운드 파일 목록을 표시합니다."""
        sounds = self._list_sounds()

        if not sounds:
            await interaction.response.send_message(
                "등록된 사운드가 없습니다. `data/sounds/` 폴더에 오디오 파일을 추가해 주세요.",
                ephemeral=True,
            )
            return

        listing = "\n".join(f"• `{s}`" for s in sounds)
        embed = discord.Embed(
            title="🔊 사운드보드",
            description=listing,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"총 {len(sounds)}개 | /사운드재생 <이름> 으로 재생")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="사운드재생", description="음성 채널에서 사운드를 재생합니다")
    @app_commands.describe(
        name="재생할 사운드 이름",
        volume="볼륨 (0.0 ~ 2.0, 기본 0.5)",
    )
    @app_commands.autocomplete(name=_sound_autocomplete)
    async def sound_play(
        self,
        interaction: discord.Interaction,
        name: str,
        volume: float = 0.5,
    ):
        """지정한 사운드를 현재 음성 채널에서 재생합니다."""
        guild = interaction.guild

        # 볼륨 범위 제한
        volume = max(0.0, min(2.0, volume))

        # 사운드 파일 확인
        sound_path = self._find_sound(name)
        if sound_path is None:
            await interaction.response.send_message(
                f"❌ `{name}` 사운드를 찾을 수 없습니다. `/사운드목록`으로 확인해 주세요.",
                ephemeral=True,
            )
            return

        # 음성 채널 접속 확인
        voice_client = guild.voice_client
        if voice_client is None:
            # 사용자가 음성 채널에 있으면 자동 접속
            if interaction.user.voice and interaction.user.voice.channel:
                try:
                    voice_client = await interaction.user.voice.channel.connect()
                except discord.ClientException as e:
                    await interaction.response.send_message(
                        f"❌ 음성 채널 접속 실패: {e}",
                        ephemeral=True,
                    )
                    return
            else:
                await interaction.response.send_message(
                    "❌ 봇이 음성 채널에 접속되어 있지 않습니다. "
                    "먼저 `/봇생성` 명령어로 봇을 음성 채널에 입장시키거나, "
                    "음성 채널에 접속한 상태에서 다시 시도해 주세요.",
                    ephemeral=True,
                )
                return

        # 이미 재생 중이면 중지
        if voice_client.is_playing():
            voice_client.stop()

        # FFmpeg 오디오 소스 생성 및 재생
        try:
            source = discord.FFmpegPCMAudio(str(sound_path))
            source = discord.PCMVolumeTransformer(source, volume=volume)
        except Exception as e:
            await interaction.response.send_message(
                f"❌ 오디오 소스 생성 실패: {e}",
                ephemeral=True,
            )
            return

        voice_client.play(source)
        await interaction.response.send_message(
            f"🔊 **{name}** 재생 중 (볼륨: {volume:.0%})",
            ephemeral=True,
        )
        logger.info(f"[{guild.name}] 사운드 재생: {name} (볼륨: {volume})")

    @app_commands.command(name="사운드정지", description="현재 재생 중인 사운드를 정지합니다")
    async def sound_stop(self, interaction: discord.Interaction):
        """재생 중인 사운드를 정지합니다."""
        guild = interaction.guild
        voice_client = guild.voice_client

        if voice_client is None or not voice_client.is_playing():
            await interaction.response.send_message(
                "현재 재생 중인 사운드가 없습니다.",
                ephemeral=True,
            )
            return

        voice_client.stop()
        await interaction.response.send_message(
            "⏹️ 사운드를 정지했습니다.",
            ephemeral=True,
        )
        logger.info(f"[{guild.name}] 사운드 정지")

    @app_commands.command(name="사운드추가", description="사운드 파일을 업로드하여 등록합니다")
    @app_commands.describe(
        file="오디오 파일 (mp3, wav, ogg, flac, m4a)",
        name="저장할 이름 (미지정 시 파일 이름 사용)",
    )
    async def sound_upload(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        name: str | None = None,
    ):
        """디스코드 첨부파일로 사운드를 등록합니다."""
        # 확장자 검증
        original = Path(file.filename)
        ext = original.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            await interaction.response.send_message(
                f"❌ 지원하지 않는 형식입니다. 지원 형식: {', '.join(SUPPORTED_EXTENSIONS)}",
                ephemeral=True,
            )
            return

        # 파일 크기 제한 (8MB)
        if file.size > 8 * 1024 * 1024:
            await interaction.response.send_message(
                "❌ 파일 크기가 8MB를 초과합니다.",
                ephemeral=True,
            )
            return

        # 저장 이름 결정
        save_name = name if name else original.stem
        # 파일명에 사용 불가한 문자 제거
        save_name = "".join(c for c in save_name if c.isalnum() or c in "-_ ")
        save_name = save_name.strip()

        if not save_name:
            await interaction.response.send_message(
                "❌ 유효한 파일 이름을 지정해 주세요.",
                ephemeral=True,
            )
            return

        save_path = SOUNDS_DIR / f"{save_name}{ext}"

        await interaction.response.defer(ephemeral=True)

        try:
            await file.save(save_path)
        except Exception as e:
            await interaction.followup.send(
                f"❌ 파일 저장 실패: {e}",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ 사운드 `{save_name}` 등록 완료! `/사운드재생 {save_name}`으로 재생하세요.",
            ephemeral=True,
        )
        logger.info(f"[{interaction.guild.name}] 사운드 등록: {save_name} ({file.filename})")

    @app_commands.command(name="사운드삭제", description="등록된 사운드를 삭제합니다")
    @app_commands.describe(name="삭제할 사운드 이름")
    @app_commands.autocomplete(name=_sound_autocomplete)
    async def sound_delete(self, interaction: discord.Interaction, name: str):
        """등록된 사운드 파일을 삭제합니다."""
        sound_path = self._find_sound(name)
        if sound_path is None:
            await interaction.response.send_message(
                f"❌ `{name}` 사운드를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        try:
            sound_path.unlink()
        except OSError as e:
            await interaction.response.send_message(
                f"❌ 삭제 실패: {e}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🗑️ `{name}` 사운드를 삭제했습니다.",
            ephemeral=True,
        )
        logger.info(f"[{interaction.guild.name}] 사운드 삭제: {name}")


async def setup(bot: commands.Bot):
    await bot.add_cog(SoundboardCog(bot))
