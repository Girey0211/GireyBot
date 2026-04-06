"""
RAG 관리 커맨드 Cog

/learn      — 텍스트 또는 파일로 지식 학습 (SQLite 저장 + ChromaDB 인덱싱)
/forget     — ID로 지식 삭제
/knowledge  — 학습된 문서 목록 확인
/knowledge-view — 문서 내용 확인
"""

import logging

import discord
from discord.ext import commands

logger = logging.getLogger("girey-bot.cogs.rag")

CATEGORY_CHOICES = [
    discord.app_commands.Choice(name="인물", value="people"),
    discord.app_commands.Choice(name="서버 규칙", value="rules"),
    discord.app_commands.Choice(name="이벤트", value="events"),
    discord.app_commands.Choice(name="일반", value="general"),
]


class RAGCog(commands.Cog, name="rag"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def _memory(self):
        return getattr(self.bot, "memory", None)

    @property
    def _ingestor(self):
        return getattr(self.bot, "rag_ingestor", None)

    @property
    def _store(self):
        return getattr(self.bot, "rag_store", None)

    def _rag_ready(self) -> bool:
        return (
            self._memory is not None
            and self._ingestor is not None
            and self._store is not None
            and self._store.is_available
        )

    # ─── /learn ─────────────────────────────────────────────────

    @discord.app_commands.command(name="learn", description="텍스트 또는 파일을 봇에게 학습시킵니다")
    @discord.app_commands.describe(
        title="문서 제목 (예: 홍길동 정보)",
        content="학습할 내용 (파일 첨부 시 생략 가능)",
        category="카테고리",
        file="학습할 파일 (.md / .txt)",
    )
    @discord.app_commands.choices(category=CATEGORY_CHOICES)
    async def learn(
        self,
        interaction: discord.Interaction,
        title: str,
        content: str | None = None,
        category: str = "general",
        file: discord.Attachment | None = None,
    ):
        if not self._rag_ready():
            await interaction.response.send_message("❌ RAG 시스템이 준비되지 않았습니다.", ephemeral=True)
            return

        if content is None and file is None:
            await interaction.response.send_message("❌ content 또는 file 중 하나를 입력하세요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # 파일이면 텍스트 추출
            if file is not None:
                raw = await file.read()
                final_content = raw.decode("utf-8", errors="ignore")
            else:
                final_content = content

            # SQLite에 저장
            doc_id = await self._memory.save_knowledge(
                title=title,
                content=final_content,
                category=category,
                author_id=interaction.user.id,
            )

            # ChromaDB에 인덱싱
            doc = await self._memory.get_knowledge(doc_id)
            stats = await self._ingestor.ingest_knowledge_doc(doc)

            embed = discord.Embed(title="✅ 학습 완료", color=discord.Color.green())
            embed.add_field(name="ID", value=str(doc_id), inline=True)
            embed.add_field(name="제목", value=title, inline=True)
            embed.add_field(name="카테고리", value=category, inline=True)
            embed.add_field(name="저장된 청크", value=f"{stats['indexed']}개", inline=True)
            embed.set_footer(text=f"/forget {doc_id} 으로 삭제할 수 있습니다")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"[RAGCog] /learn 실패: {e}")
            await interaction.followup.send(f"❌ 오류: {e}", ephemeral=True)

    # ─── /forget ────────────────────────────────────────────────

    @discord.app_commands.command(name="forget", description="학습된 문서를 삭제합니다")
    @discord.app_commands.describe(doc_id="삭제할 문서 ID (/knowledge 로 확인)")
    async def forget(self, interaction: discord.Interaction, doc_id: int):
        if not self._rag_ready():
            await interaction.response.send_message("❌ RAG 시스템이 준비되지 않았습니다.", ephemeral=True)
            return

        doc = await self._memory.get_knowledge(doc_id)
        if doc is None:
            await interaction.response.send_message(f"❌ ID {doc_id} 문서를 찾을 수 없습니다.", ephemeral=True)
            return

        # SQLite + ChromaDB 동시 삭제
        await self._memory.delete_knowledge(doc_id)
        self._ingestor.forget_doc(doc_id)

        await interaction.response.send_message(
            f"🗑️ `{doc.title}` (ID: {doc_id}) 삭제 완료", ephemeral=True
        )

    # ─── /knowledge ─────────────────────────────────────────────

    @discord.app_commands.command(name="knowledge", description="학습된 문서 목록을 확인합니다")
    @discord.app_commands.describe(category="카테고리 필터 (생략 시 전체)")
    @discord.app_commands.choices(category=CATEGORY_CHOICES)
    async def knowledge(
        self,
        interaction: discord.Interaction,
        category: str | None = None,
    ):
        if not self._memory:
            await interaction.response.send_message("❌ 메모리 시스템이 준비되지 않았습니다.", ephemeral=True)
            return

        docs = await self._memory.list_knowledge(category=category)

        embed = discord.Embed(title="📚 학습된 지식 목록", color=discord.Color.blue())

        if not docs:
            embed.description = "학습된 문서가 없습니다."
        else:
            lines = []
            for doc in docs[:20]:
                preview = doc.content[:40].replace("\n", " ")
                lines.append(f"`{doc.id}` **{doc.title}** [{doc.category}]\n　{preview}…")
            embed.description = "\n\n".join(lines)
            if len(docs) > 20:
                embed.set_footer(text=f"총 {len(docs)}개 중 20개 표시")
            else:
                embed.set_footer(text=f"총 {len(docs)}개")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── /knowledge-view ────────────────────────────────────────

    @discord.app_commands.command(name="knowledge-view", description="문서 내용을 확인합니다")
    @discord.app_commands.describe(doc_id="확인할 문서 ID")
    async def knowledge_view(self, interaction: discord.Interaction, doc_id: int):
        if not self._memory:
            await interaction.response.send_message("❌ 메모리 시스템이 준비되지 않았습니다.", ephemeral=True)
            return

        doc = await self._memory.get_knowledge(doc_id)
        if doc is None:
            await interaction.response.send_message(f"❌ ID {doc_id} 문서를 찾을 수 없습니다.", ephemeral=True)
            return

        content_preview = doc.content[:1000] + ("…" if len(doc.content) > 1000 else "")

        embed = discord.Embed(
            title=f"📄 {doc.title}",
            description=f"```{content_preview}```",
            color=discord.Color.greyple(),
        )
        embed.add_field(name="ID", value=str(doc.id), inline=True)
        embed.add_field(name="카테고리", value=doc.category, inline=True)
        embed.add_field(name="작성일", value=doc.created_at[:10], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RAGCog(bot))
