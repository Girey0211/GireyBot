"""
스킬 실행기 — SKILL.md 지시문 기반 LLM 에이전트

선택된 스킬의 마크다운 본문을 system prompt에 주입하고,
LLM이 도구를 호출하면 실제 도구를 실행한 뒤 결과를 피드백하는
에이전트 루프를 수행합니다.
"""

import json
import logging

import discord

from core.llm.base import BaseLLMClient
from core.skills.models import Skill, SkillMatchResult, SkillMatchCandidate
from core.tools.base import BaseExecutor, CommandResult
from core.tools.ssh import SSHExecutor
from core.tools.local import LocalExecutor
from core.tools.docker import DockerExecutor

logger = logging.getLogger("girey-bot.skills.executor")


def _create_executor(skill: Skill) -> BaseExecutor | None:
    """스킬의 executor 타입과 config에 따라 적절한 실행기를 생성합니다."""
    executor_type = skill.executor
    config = skill.config

    if executor_type == "ssh":
        host = config.get("host")
        if not host:
            logger.warning(f"스킬 '{skill.name}': SSH executor에 host가 없음")
            return None
        return SSHExecutor(
            host=host,
            port=config.get("port", 22),
            username=config.get("username", "root"),
            password=config.get("password"),
            ssh_key_path=config.get("ssh_key_path"),
            allowed_commands=config.get("allowed_commands"),
        )

    if executor_type == "docker":
        return DockerExecutor(
            working_dir=config.get("working_dir"),
            allowed_commands=config.get("allowed_commands"),
        )

    if executor_type == "local":
        return LocalExecutor(
            allowed_commands=config.get("allowed_commands"),
            working_dir=config.get("working_dir"),
        )

    return None


class SkillExecutor:
    """스킬 지시문 기반 LLM 실행기"""

    def __init__(
        self,
        llm_client: BaseLLMClient,
    ):
        self.llm_client = llm_client

    async def execute(
        self,
        skill: Skill,
        user_message: str,
        context: str | None = None,
    ) -> str:
        """
        스킬을 실행하고 LLM 응답을 반환합니다.

        executor가 지정된 스킬은 LLM 응답에서 명령을 추출하여
        실행기로 실제 명령을 수행한 뒤, 결과를 LLM에 다시 피드백합니다.
        """
        executor = _create_executor(skill)
        system_prompt = self._build_system_prompt(skill, executor)

        # 1차: LLM에게 의도 분석 + 실행할 명령 요청
        response = await self.llm_client.chat(
            prompt=user_message,
            system_prompt=system_prompt,
            context=context,
        )

        if not response.available or not response.content:
            return (
                f"스킬 `{skill.name}` 실행 중 오류가 발생했습니다.\n"
                f"`{response.reason or '알 수 없는 오류'}`"
            )

        # executor가 없으면 LLM 응답만 반환 (프롬프트 기반 스킬)
        if executor is None:
            return response.content

        # executor가 있으면 LLM 응답에서 명령을 추출하여 실행
        commands = self._extract_commands(response.content)
        if not commands:
            return response.content

        # 명령 실행 + 결과 수집
        results: list[CommandResult] = []
        for cmd in commands:
            result = await executor.execute(cmd)
            results.append(result)
            logger.info(
                f"[{skill.name}] {executor.executor_type}> {cmd} "
                f"→ exit={result.exit_code}"
            )

        # 2차: 실행 결과를 LLM에게 피드백하여 최종 응답 생성
        result_summary = self._format_results(results, commands)
        followup_prompt = (
            f"명령 실행 결과입니다. 사용자에게 보여줄 최종 응답을 생성하세요.\n\n"
            f"{result_summary}"
        )

        final_response = await self.llm_client.chat(
            prompt=followup_prompt,
            system_prompt=system_prompt,
            context=context,
        )

        if final_response.available and final_response.content:
            return final_response.content

        # 폴백: 실행 결과 직접 반환
        return result_summary

    def _build_system_prompt(
        self,
        skill: Skill,
        executor: BaseExecutor | None = None,
    ) -> str:
        """스킬 지시문 + 실행기 정보를 결합한 시스템 프롬프트를 생성합니다."""
        parts = [
            f"# 현재 활성 스킬: {skill.name}",
            f"설명: {skill.description}",
            "",
            "## 스킬 지시문",
            skill.body,
        ]

        if skill.config:
            parts.extend([
                "",
                "## 스킬 설정 (서버별 오버라이드)",
                f"```json\n{json.dumps(skill.config, ensure_ascii=False, indent=2)}\n```",
            ])

        if executor is not None:
            parts.extend([
                "",
                f"## 명령 실행 환경: {executor.executor_type}",
                "실행할 명령이 있으면 반드시 아래 형식으로 출력하세요:",
                "```command",
                "<실행할 명령>",
                "```",
                "여러 명령을 순서대로 실행해야 하면 각각 별도의 command 블록으로 작성하세요.",
            ])

        return "\n".join(parts)

    @staticmethod
    def _extract_commands(llm_response: str) -> list[str]:
        """LLM 응답에서 ```command 블록을 추출합니다."""
        import re
        pattern = r"```command\s*\n(.+?)\n```"
        matches = re.findall(pattern, llm_response, re.DOTALL)
        return [cmd.strip() for cmd in matches if cmd.strip()]

    @staticmethod
    def _format_results(
        results: list[CommandResult],
        commands: list[str],
    ) -> str:
        """명령 실행 결과를 텍스트로 포맷합니다."""
        lines = []
        for cmd, result in zip(commands, results):
            lines.append(f"### `{cmd}`")
            if result.success and result.exit_code == 0:
                lines.append(f"✅ 성공 (exit code: {result.exit_code})")
            else:
                lines.append(f"❌ 실패 (exit code: {result.exit_code})")

            if result.stdout:
                lines.append(f"```\n{result.stdout}\n```")
            if result.stderr:
                lines.append(f"stderr:\n```\n{result.stderr}\n```")
            if result.error:
                lines.append(f"오류: {result.error}")
            lines.append("")

        return "\n".join(lines)

    # ─── 재확인 UI ──────────────

    @staticmethod
    async def send_clarification(
        message: discord.Message,
        match_result: SkillMatchResult,
    ) -> "ClarificationView":
        """
        신뢰도가 낮을 때 사용자에게 스킬 선택을 재확인하는 UI를 전송합니다.

        Discord Button 컴포넌트로 후보 스킬 목록을 제시하고,
        ClarificationView를 반환하여 호출자가 await view.wait()로 대기할 수 있게 합니다.
        """
        candidates = match_result.candidates[:5]  # 최대 5개

        embed = discord.Embed(
            title="🤔 어떤 기능을 실행할까요?",
            description=(
                "요청을 분석했지만 확신이 부족합니다.\n"
                "아래에서 원하는 기능을 선택해주세요."
            ),
            color=discord.Color.gold(),
        )

        for i, candidate in enumerate(candidates, 1):
            embed.add_field(
                name=f"{i}. {candidate.skill.name}",
                value=(
                    f"{candidate.skill.description}\n"
                    f"신뢰도: {candidate.confidence:.0f}% · {candidate.reason}"
                ),
                inline=False,
            )

        view = ClarificationView(candidates, timeout=30)
        view.message = await message.reply(
            embed=embed,
            view=view,
            mention_author=False,
        )
        return view

    @staticmethod
    async def send_skill_result(
        message: discord.Message,
        skill: Skill,
        result_text: str,
    ) -> None:
        """스킬 실행 결과를 Embed로 전송합니다."""
        max_len = 4000
        if len(result_text) > max_len:
            result_text = result_text[:max_len - 20] + "\n\n…(잘렸습니다)"

        embed = discord.Embed(
            description=result_text,
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"스킬: {skill.name}")

        await message.reply(embed=embed, mention_author=False)


class ClarificationView(discord.ui.View):
    """신뢰도 미달 시 사용자에게 스킬 선택을 요청하는 버튼 View"""

    def __init__(
        self,
        candidates: list[SkillMatchCandidate],
        timeout: float = 30,
    ):
        super().__init__(timeout=timeout)
        self.candidates = candidates
        self.selected_skill: Skill | None = None
        self.message: discord.Message | None = None

        for i, candidate in enumerate(candidates):
            button = ClarificationButton(
                skill=candidate.skill,
                label=candidate.skill.name,
                index=i,
            )
            self.add_item(button)

        # "아무것도 아님" 취소 버튼
        cancel = discord.ui.Button(
            label="취소",
            style=discord.ButtonStyle.secondary,
            custom_id="skill_cancel",
        )
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        self.selected_skill = None
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                description="취소되었습니다.",
                color=discord.Color.light_grey(),
            ),
            view=None,
        )

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(
                    embed=discord.Embed(
                        description="⏰ 시간이 초과되었습니다.",
                        color=discord.Color.light_grey(),
                    ),
                    view=None,
                )
            except discord.HTTPException:
                pass


class ClarificationButton(discord.ui.Button):
    """개별 스킬 선택 버튼"""

    def __init__(self, skill: Skill, label: str, index: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"skill_select_{index}",
        )
        self.skill = skill

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ClarificationView = self.view  # type: ignore
        view.selected_skill = self.skill
        view.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"**{self.skill.name}** 스킬을 실행합니다…",
                color=discord.Color.green(),
            ),
            view=None,
        )
