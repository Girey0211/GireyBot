"""
스킬 슬래시 명령어 Cog

/skill <name> [args]             — 스킬 실행
/skills                          — 스킬 목록
/skills-create                   — 새 스킬 생성
/skills-edit <name>              — 스킬 본문 편집
/skills-generate <name> <prompt> — LLM으로 실행 절차 자동 생성
/skills-info <name>              — 스킬 상세 정보
/skills-delete <name>            — 스킬 삭제
/skills-setup <name>             — 접속 정보 설정
/skills-reload                   — 리로드 (관리자 전용)
"""

import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from core.skills.loader import CREDENTIALS_DIR, GLOBAL_SKILLS_DIR
from core.skills.models import Skill

if TYPE_CHECKING:
    from core.agent import GireyBot

logger = logging.getLogger("girey-bot.cogs.skill_commands")

# YAML frontmatter 구분자 패턴
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


# ── 공통 유틸 ─────────────────────────────────────────────────

def _reload_skills(bot: "GireyBot") -> tuple[int, int]:
    """스킬 리로드 후 (활성, 미설정) 개수를 반환합니다."""
    skills = bot.skill_loader.load_all()
    if skills and bot.skill_router:
        bot.skill_router.update_skills(skills)
    elif skills:
        from core.skills.router import SkillRouter
        bot.skill_router = SkillRouter(skills, bot.llm_client)
    return len(skills), len(bot.skill_loader.unconfigured_skills)


def _save_credentials(cred_filename: str, cred_data: dict) -> None:
    """credentials YAML 파일을 저장합니다."""
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    cred_path = CREDENTIALS_DIR / cred_filename
    cred_path.write_text(
        yaml.dump(cred_data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    logger.info(f"credentials 저장: {cred_path}")


def _find_skill_md(name: str) -> Path | None:
    """스킬 이름으로 SKILL.md 경로를 찾습니다."""
    path = GLOBAL_SKILLS_DIR / name / "SKILL.md"
    return path if path.exists() else None


def _read_skill_md(name: str) -> tuple[str, str] | None:
    """SKILL.md를 읽어 (frontmatter_yaml, body) 튜플을 반환합니다."""
    path = _find_skill_md(name)
    if path is None:
        return None
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def _write_skill_md(name: str, frontmatter_yaml: str, body: str) -> None:
    """frontmatter와 body를 결합하여 SKILL.md를 덮어씁니다."""
    path = GLOBAL_SKILLS_DIR / name / "SKILL.md"
    content = f"---\n{frontmatter_yaml.strip()}\n---\n\n{body.strip()}\n"
    path.write_text(content, encoding="utf-8")
    logger.info(f"SKILL.md 저장: {path}")


def _all_skill_names() -> list[str]:
    """skills/ 하위 디렉토리 중 SKILL.md가 있는 이름 목록을 반환합니다."""
    if not GLOBAL_SKILLS_DIR.is_dir():
        return []
    return sorted(
        d.name for d in GLOBAL_SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )


# ── Modal: 비밀번호 인증 ──────────────────────────────────────

class PasswordCredentialModal(discord.ui.Modal):
    """비밀번호 기반 SSH 접속 정보를 입력받습니다."""

    host = discord.ui.TextInput(
        label="호스트 (IP 또는 도메인)",
        placeholder="192.168.1.100",
        required=True,
        max_length=255,
    )
    port = discord.ui.TextInput(
        label="SSH 포트",
        placeholder="22",
        default="22",
        required=True,
        max_length=5,
    )
    username = discord.ui.TextInput(
        label="사용자명",
        placeholder="mc-admin",
        required=True,
        max_length=64,
    )
    password = discord.ui.TextInput(
        label="비밀번호",
        placeholder="SSH 접속 비밀번호",
        required=True,
        style=discord.TextStyle.short,
        max_length=500,
    )
    extra = discord.ui.TextInput(
        label="추가 설정 (YAML, 선택)",
        placeholder="service_name: minecraft.service\nallowed_roles:\n  - 서버 관리자",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, skill_name: str, cred_filename: str, bot: "GireyBot"):
        super().__init__(title=f"🔑 {skill_name} — 비밀번호 인증")
        self.skill_name = skill_name
        self.cred_filename = cred_filename
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        port_str = self.port.value.strip()
        if not port_str.isdigit():
            await interaction.response.send_message(
                "포트는 숫자여야 합니다.", ephemeral=True,
            )
            return

        cred_data: dict = {
            "host": self.host.value.strip(),
            "port": int(port_str),
            "username": self.username.value.strip(),
            "password": self.password.value.strip(),
        }

        if self.extra.value and self.extra.value.strip():
            try:
                extra_data = yaml.safe_load(self.extra.value.strip())
                if isinstance(extra_data, dict):
                    cred_data.update(extra_data)
            except yaml.YAMLError:
                await interaction.response.send_message(
                    "추가 설정 YAML 파싱에 실패했습니다.", ephemeral=True,
                )
                return

        _save_credentials(self.cred_filename, cred_data)
        active, _ = _reload_skills(self.bot)
        loaded = self.skill_name in self.bot.skill_loader.skills

        if loaded:
            await interaction.response.send_message(
                f"✅ **{self.skill_name}** 접속 정보가 저장되었습니다.\n"
                f"스킬이 활성화되었습니다. `/skill {self.skill_name}`으로 사용하세요.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "⚠️ 접속 정보는 저장되었으나 스킬 로드에 실패했습니다.\n"
                "로그를 확인하세요.",
                ephemeral=True,
            )


# ── Modal: SSH 키 인증 ────────────────────────────────────────

class SSHKeyCredentialModal(discord.ui.Modal):
    """SSH 키 기반 접속 정보를 입력받습니다."""

    host = discord.ui.TextInput(
        label="호스트 (IP 또는 도메인)",
        placeholder="192.168.1.100",
        required=True,
        max_length=255,
    )
    port = discord.ui.TextInput(
        label="SSH 포트",
        placeholder="22",
        default="22",
        required=True,
        max_length=5,
    )
    username = discord.ui.TextInput(
        label="사용자명",
        placeholder="mc-admin",
        required=True,
        max_length=64,
    )
    ssh_key_path = discord.ui.TextInput(
        label="SSH 키 파일 경로",
        placeholder="/home/user/.ssh/id_rsa",
        required=True,
        style=discord.TextStyle.short,
        max_length=500,
    )
    extra = discord.ui.TextInput(
        label="추가 설정 (YAML, 선택)",
        placeholder="service_name: minecraft.service\nallowed_roles:\n  - 서버 관리자",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, skill_name: str, cred_filename: str, bot: "GireyBot"):
        super().__init__(title=f"🔐 {skill_name} — SSH 키 인증")
        self.skill_name = skill_name
        self.cred_filename = cred_filename
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        port_str = self.port.value.strip()
        if not port_str.isdigit():
            await interaction.response.send_message(
                "포트는 숫자여야 합니다.", ephemeral=True,
            )
            return

        cred_data: dict = {
            "host": self.host.value.strip(),
            "port": int(port_str),
            "username": self.username.value.strip(),
            "ssh_key_path": self.ssh_key_path.value.strip(),
        }

        if self.extra.value and self.extra.value.strip():
            try:
                extra_data = yaml.safe_load(self.extra.value.strip())
                if isinstance(extra_data, dict):
                    cred_data.update(extra_data)
            except yaml.YAMLError:
                await interaction.response.send_message(
                    "추가 설정 YAML 파싱에 실패했습니다.", ephemeral=True,
                )
                return

        _save_credentials(self.cred_filename, cred_data)
        _reload_skills(self.bot)
        loaded = self.skill_name in self.bot.skill_loader.skills

        if loaded:
            await interaction.response.send_message(
                f"✅ **{self.skill_name}** 접속 정보가 저장되었습니다.\n"
                f"스킬이 활성화되었습니다. `/skill {self.skill_name}`으로 사용하세요.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "⚠️ 접속 정보는 저장되었으나 스킬 로드에 실패했습니다.\n"
                "로그를 확인하세요.",
                ephemeral=True,
            )


# ── 인증 방식 선택 View ───────────────────────────────────────

class AuthTypeSelectView(discord.ui.View):
    """비밀번호 / SSH 키 인증 방식을 선택하는 버튼 View"""

    def __init__(
        self,
        skill_name: str,
        cred_filename: str,
        bot: "GireyBot",
        *,
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.skill_name = skill_name
        self.cred_filename = cred_filename
        self.bot = bot

    @discord.ui.button(label="비밀번호 인증", style=discord.ButtonStyle.primary, emoji="🔑")
    async def password_auth(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        modal = PasswordCredentialModal(
            self.skill_name, self.cred_filename, self.bot,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="SSH 키 인증", style=discord.ButtonStyle.secondary, emoji="🔐")
    async def ssh_key_auth(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        modal = SSHKeyCredentialModal(
            self.skill_name, self.cred_filename, self.bot,
        )
        await interaction.response.send_modal(modal)


# ── /skills 의 미설정 스킬 설정 버튼 ──────────────────────────

class SetupButtonView(discord.ui.View):
    """미설정 스킬별 '설정' 버튼을 표시하는 View"""

    def __init__(
        self,
        unconfigured: dict[str, str],
        bot: "GireyBot",
        *,
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)
        for skill_name, cred_file in unconfigured.items():
            self.add_item(SetupButton(skill_name, cred_file, bot))


class SetupButton(discord.ui.Button["SetupButtonView"]):
    """개별 스킬 설정 버튼 — 클릭 시 인증 방식 선택 표시"""

    def __init__(self, skill_name: str, cred_file: str, bot: "GireyBot"):
        super().__init__(
            label=f"{skill_name} 설정",
            style=discord.ButtonStyle.primary,
            emoji="🔧",
        )
        self.skill_name = skill_name
        self.cred_file = cred_file
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "관리자만 설정할 수 있습니다.", ephemeral=True,
            )
            return

        view = AuthTypeSelectView(self.skill_name, self.cred_file, self.bot)
        await interaction.response.send_message(
            f"**{self.skill_name}** 인증 방식을 선택하세요.\n"
            "입력 내용은 채널에 기록되지 않으며, 봇에게만 전송됩니다.",
            view=view,
            ephemeral=True,
        )


# ── Modal: 스킬 생성 (기본 정보) ──────────────────────────────

_EXECUTOR_CHOICES = {"ssh", "local", "docker", ""}

_SKILL_BODY_TEMPLATE = """\
# {name}

## 트리거 조건

- 사용자가 관련 키워드를 포함한 메시지를 보낼 때 활성화됩니다.
- `/skill {name}` 슬래시 명령어로 직접 실행할 수 있습니다.

## 의도 분류

사용자 메시지에서 아래 의도를 파악한다:

| 의도 | 키워드 예시 |
| ------ | ------------ |
| `status` | 상태, 확인, status |

의도를 파악할 수 없으면 사용자에게 되물어본다.

## 실행 절차

1. (여기에 실행 절차를 작성하세요)

## 출력 형식

Embed 메시지로 응답한다.

## 에러 처리

- 연결 실패 → "서버에 접근할 수 없습니다."
- 권한 부족 → "실행 권한이 없습니다."
"""


class SkillCreateModal(discord.ui.Modal):
    """새 스킬의 기본 정보를 입력받습니다."""

    skill_name = discord.ui.TextInput(
        label="스킬 이름 (영문, 하이픈 가능)",
        placeholder="my-server",
        required=True,
        max_length=64,
    )
    description = discord.ui.TextInput(
        label="설명",
        placeholder="원격 서버를 관리합니다.",
        required=True,
        max_length=200,
    )
    triggers = discord.ui.TextInput(
        label="트리거 키워드 (쉼표로 구분)",
        placeholder="서버, server, 상태확인",
        required=False,
        max_length=500,
    )
    executor = discord.ui.TextInput(
        label="실행기 (ssh / local / docker / 비워두면 없음)",
        placeholder="ssh",
        required=False,
        max_length=10,
    )
    credentials_file = discord.ui.TextInput(
        label="credentials 파일명 (선택, executor가 있을 때)",
        placeholder="my-server.yaml",
        required=False,
        max_length=100,
    )

    def __init__(self, bot: "GireyBot"):
        super().__init__(title="✨ 새 스킬 만들기")
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.skill_name.value.strip().lower().replace(" ", "-")

        # 이름 검증
        if not name or not all(c.isalnum() or c == "-" for c in name):
            await interaction.response.send_message(
                "스킬 이름은 영문, 숫자, 하이픈(-)만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        # 중복 검사
        skill_dir = GLOBAL_SKILLS_DIR / name
        if skill_dir.exists():
            await interaction.response.send_message(
                f"스킬 `{name}` 디렉토리가 이미 존재합니다.",
                ephemeral=True,
            )
            return

        # executor 검증
        executor_val = self.executor.value.strip().lower()
        if executor_val and executor_val not in _EXECUTOR_CHOICES:
            await interaction.response.send_message(
                f"실행기는 ssh, local, docker 중 하나여야 합니다. "
                f"(입력: `{executor_val}`)",
                ephemeral=True,
            )
            return

        # 트리거 파싱
        trigger_list = [
            t.strip() for t in self.triggers.value.split(",")
            if t.strip()
        ] if self.triggers.value else []

        # credentials 파일명
        cred_file = self.credentials_file.value.strip() or None
        if cred_file and not cred_file.endswith(".yaml"):
            cred_file += ".yaml"

        # SKILL.md frontmatter 생성
        frontmatter: dict = {
            "name": name,
            "description": self.description.value.strip(),
        }
        if trigger_list:
            frontmatter["triggers"] = trigger_list
        frontmatter["user-invocable"] = True
        if executor_val:
            frontmatter["executor"] = executor_val
        if cred_file:
            frontmatter["credentials"] = cred_file

        body = _SKILL_BODY_TEMPLATE.format(name=name)
        frontmatter_yaml = yaml.dump(
            frontmatter, allow_unicode=True, default_flow_style=False,
        ).strip()
        skill_md_content = f"---\n{frontmatter_yaml}\n---\n\n{body}"

        # 디렉토리 및 파일 생성
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_md_content, encoding="utf-8")
        logger.info(f"스킬 생성: {name} → {skill_dir / 'SKILL.md'}")

        # example credentials 생성
        if cred_file and executor_val == "ssh":
            example_name = cred_file.replace(".yaml", ".example.yaml")
            example_path = CREDENTIALS_DIR / example_name
            CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
            if not example_path.exists():
                example_path.write_text(
                    f"# {cred_file} 작성용 템플릿\n"
                    f"# 이 파일을 {cred_file}로 복사하여 실제 값을 입력하세요.\n\n"
                    'host: "192.168.1.100"\n'
                    "port: 22\n"
                    'username: "admin"\n\n'
                    "# 인증 — 둘 중 하나만 사용\n"
                    'ssh_key_path: "/home/user/.ssh/id_rsa"\n'
                    '# password: "your-password"\n\n'
                    "# 권한\n"
                    "allowed_roles:\n"
                    '  - "관리자"\n',
                    encoding="utf-8",
                )

        # 스킬 리로드
        _reload_skills(self.bot)

        # 결과 Embed
        embed = discord.Embed(
            title=f"✨ 스킬 `{name}` 생성 완료",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="파일", value=f"`skills/{name}/SKILL.md`", inline=True,
        )
        if executor_val:
            embed.add_field(name="실행기", value=executor_val, inline=True)
        if trigger_list:
            embed.add_field(
                name="트리거", value=", ".join(trigger_list), inline=False,
            )

        # credentials 설정이 필요하면 안내
        needs_cred_setup = cred_file and name in self.bot.skill_loader.unconfigured_skills
        if needs_cred_setup:
            embed.add_field(
                name="⚠️ 접속 정보 설정 필요",
                value=(
                    f"`data/credentials/{cred_file}` 파일이 필요합니다.\n"
                    "아래 버튼으로 바로 설정하거나, 서버에서 직접 파일을 생성하세요."
                ),
                inline=False,
            )
            embed.set_footer(
                text="SKILL.md 본문은 서버에서 직접 수정하여 실행 절차를 작성하세요."
            )
            view = AuthTypeSelectView(name, cred_file, self.bot)
            await interaction.response.send_message(
                embed=embed, view=view, ephemeral=True,
            )
        else:
            if self.bot.skill_loader.get_skill(name):
                embed.set_footer(text=f"활성화됨 · /skill {name} 으로 실행")
            else:
                embed.set_footer(
                    text="SKILL.md 본문은 서버에서 직접 수정하여 실행 절차를 작성하세요."
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Modal: 스킬 본문 편집 ──────────────────────────────────────

class SkillEditModal(discord.ui.Modal):
    """기존 스킬의 본문(실행 절차)을 편집합니다."""

    body = discord.ui.TextInput(
        label="스킬 본문 (마크다운)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000,
    )

    def __init__(self, skill_name: str, frontmatter_yaml: str, current_body: str, bot: "GireyBot"):
        super().__init__(title=f"📝 {skill_name} 본문 편집")
        self.skill_name = skill_name
        self.frontmatter_yaml = frontmatter_yaml
        self.bot = bot
        self.body.default = current_body[:4000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        new_body = self.body.value
        _write_skill_md(self.skill_name, self.frontmatter_yaml, new_body)
        _reload_skills(self.bot)

        await interaction.response.send_message(
            f"✅ `{self.skill_name}` 본문이 저장되었습니다.\n"
            f"글자 수: {len(new_body)}자",
            ephemeral=True,
        )


# ── View: LLM 생성 결과 적용/편집/취소 ────────────────────────

class GenerateResultView(discord.ui.View):
    """LLM이 생성한 본문을 적용, 편집, 또는 취소합니다."""

    def __init__(
        self,
        skill_name: str,
        frontmatter_yaml: str,
        generated_body: str,
        bot: "GireyBot",
        *,
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)
        self.skill_name = skill_name
        self.frontmatter_yaml = frontmatter_yaml
        self.generated_body = generated_body
        self.bot = bot

    @discord.ui.button(label="바로 적용", style=discord.ButtonStyle.success, emoji="✅")
    async def apply_directly(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        _write_skill_md(self.skill_name, self.frontmatter_yaml, self.generated_body)
        _reload_skills(self.bot)
        await interaction.response.edit_message(
            content=f"✅ `{self.skill_name}` 본문이 적용되었습니다.",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="편집 후 적용", style=discord.ButtonStyle.primary, emoji="📝")
    async def edit_then_apply(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        modal = SkillEditModal(
            self.skill_name, self.frontmatter_yaml, self.generated_body, self.bot,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel_generate(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content="생성 결과가 취소되었습니다.",
            embed=None,
            view=None,
        )


# ── View: 스킬 삭제 확인 ──────────────────────────────────────

class SkillDeleteConfirmView(discord.ui.View):
    """스킬 삭제 전 확인 버튼"""

    def __init__(self, skill_name: str, bot: "GireyBot", *, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.skill_name = skill_name
        self.bot = bot

    @discord.ui.button(label="삭제 확인", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_delete(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        skill_dir = GLOBAL_SKILLS_DIR / self.skill_name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            logger.info(f"스킬 삭제: {skill_dir}")

        _reload_skills(self.bot)
        await interaction.response.edit_message(
            content=f"🗑️ 스킬 `{self.skill_name}`이(가) 삭제되었습니다.",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel_delete(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content="삭제가 취소되었습니다.",
            embed=None,
            view=None,
        )


# ── Cog ───────────────────────────────────────────────────────

class SkillCommands(commands.Cog):
    """스킬 관련 슬래시 명령어"""

    def __init__(self, bot: "GireyBot"):
        self.bot = bot

    # ── /skill ────────────────────────────────────────────────

    @app_commands.command(name="skill", description="스킬을 실행합니다")
    @app_commands.describe(
        name="실행할 스킬 이름",
        args="스킬에 전달할 인자 (선택)",
    )
    async def skill_command(
        self,
        interaction: discord.Interaction,
        name: str,
        args: str | None = None,
    ):
        """슬래시 명령어로 스킬을 직접 실행합니다."""
        if not self.bot.skill_router:
            await interaction.response.send_message(
                "스킬 시스템이 초기화되지 않았습니다.",
                ephemeral=True,
            )
            return

        match_result = self.bot.skill_router.route_direct(name)

        if match_result.skill is None:
            available = ", ".join(
                s.name for s in self.bot.skill_loader.get_invocable_skills()
            )
            await interaction.response.send_message(
                f"스킬 `{name}`을(를) 찾을 수 없습니다.\n"
                f"사용 가능: {available or '(없음)'}",
                ephemeral=True,
            )
            return

        if not self._check_permission(interaction, match_result.skill):
            await interaction.response.send_message(
                "이 스킬을 실행할 권한이 없습니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        skill = match_result.skill
        user_message = args or f"/{name} 실행"
        context_mode = skill.metadata.get("context_mode")

        if context_mode == "session":
            gap = skill.metadata.get("session_gap_minutes", 10)
            session = await self.bot.memory.get_conversation_session(
                channel_id=interaction.channel_id,
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
            context = await self.bot.memory.build_context(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                user_id=interaction.user.id,
            )

        result_text = await self.bot.skill_executor.execute(
            skill=skill,
            user_message=user_message,
            context=context,
        )

        embed = discord.Embed(
            description=result_text[:4000],
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"스킬: {skill.name}")
        await interaction.followup.send(embed=embed)

    @skill_command.autocomplete("name")
    async def skill_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """스킬 이름 자동완성"""
        if not self.bot.skill_loader:
            return []

        invocable = self.bot.skill_loader.get_invocable_skills()
        return [
            app_commands.Choice(name=f"{s.name} — {s.description[:50]}", value=s.name)
            for s in invocable
            if current.lower() in s.name.lower()
        ][:25]

    # ── /skills ───────────────────────────────────────────────

    @app_commands.command(name="skills", description="사용 가능한 스킬 목록을 표시합니다")
    async def skills_list(self, interaction: discord.Interaction):
        """로드된 스킬 목록을 표시합니다."""
        if not self.bot.skill_loader:
            await interaction.response.send_message(
                "스킬 시스템이 초기화되지 않았습니다.",
                ephemeral=True,
            )
            return

        skills = self.bot.skill_loader.get_invocable_skills()
        unconfigured = self.bot.skill_loader.unconfigured_skills

        if not skills and not unconfigured:
            await interaction.response.send_message(
                "사용 가능한 스킬이 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="📋 스킬 목록",
            color=discord.Color.blue(),
        )

        for skill in skills:
            triggers = ", ".join(skill.triggers[:5]) if skill.triggers else "(없음)"
            embed.add_field(
                name=f"✅ `{skill.name}`",
                value=f"{skill.description}\n트리거: {triggers}",
                inline=False,
            )

        view = None
        if unconfigured and interaction.user.guild_permissions.administrator:
            setup_lines = []
            for name, cred_file in unconfigured.items():
                example = cred_file.replace(".yaml", ".example.yaml")
                setup_lines.append(
                    f"**{name}** — `data/credentials/{cred_file}` 필요\n"
                    f"  템플릿: `data/credentials/{example}`"
                )

            embed.add_field(
                name="⚠️ 설정이 필요한 스킬",
                value=(
                    "\n".join(setup_lines) + "\n\n"
                    "아래 버튼을 눌러 Discord에서 바로 설정하거나,\n"
                    "봇 서버에서 `.example.yaml`을 복사하여 설정하세요."
                ),
                inline=False,
            )
            view = SetupButtonView(unconfigured, self.bot)

        active = len(skills)
        pending = len(unconfigured)
        footer = f"활성: {active}개"
        if pending:
            footer += f" · 미설정: {pending}개"
        embed.set_footer(text=f"{footer} · /skill <이름> 으로 실행")

        if view:
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed)

    # ── /skills-setup ─────────────────────────────────────────

    @app_commands.command(
        name="skills-setup",
        description="Discord에서 스킬 접속 정보를 설정합니다 (관리자 전용)",
    )
    # @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(name="설정할 스킬 이름")
    async def skills_setup(
        self,
        interaction: discord.Interaction,
        name: str,
    ):
        """인증 방식 선택 → Modal 팝업으로 접속 정보를 입력받습니다."""
        if not self.bot.skill_loader:
            await interaction.response.send_message(
                "스킬 시스템이 초기화되지 않았습니다.",
                ephemeral=True,
            )
            return

        unconfigured = self.bot.skill_loader.unconfigured_skills
        if name not in unconfigured:
            if self.bot.skill_loader.get_skill(name):
                await interaction.response.send_message(
                    f"스킬 `{name}`은(는) 이미 설정되어 있습니다.",
                    ephemeral=True,
                )
            else:
                available = ", ".join(unconfigured.keys()) or "(없음)"
                await interaction.response.send_message(
                    f"설정이 필요한 스킬 `{name}`을(를) 찾을 수 없습니다.\n"
                    f"미설정 스킬: {available}",
                    ephemeral=True,
                )
            return

        view = AuthTypeSelectView(name, unconfigured[name], self.bot)
        await interaction.response.send_message(
            f"**{name}** 인증 방식을 선택하세요.\n"
            "입력 내용은 채널에 기록되지 않으며, 봇에게만 전송됩니다.",
            view=view,
            ephemeral=True,
        )

    @skills_setup.autocomplete("name")
    async def skills_setup_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """미설정 스킬 이름 자동완성"""
        if not self.bot.skill_loader:
            return []
        return [
            app_commands.Choice(name=name, value=name)
            for name in self.bot.skill_loader.unconfigured_skills
            if current.lower() in name.lower()
        ][:25]

    # ── /skills-create ────────────────────────────────────────

    @app_commands.command(
        name="skills-create",
        description="대화형으로 새 스킬을 만듭니다 (관리자 전용)",
    )
    # @app_commands.checks.has_permissions(administrator=True)
    async def skills_create(self, interaction: discord.Interaction):
        """Modal로 스킬 기본 정보를 입력받아 SKILL.md를 생성합니다."""
        modal = SkillCreateModal(self.bot)
        await interaction.response.send_modal(modal)

    # ── /skills-edit ──────────────────────────────────────────

    @app_commands.command(
        name="skills-edit",
        description="스킬의 실행 절차(본문)를 편집합니다 (관리자 전용)",
    )
    # @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(name="편집할 스킬 이름")
    async def skills_edit(self, interaction: discord.Interaction, name: str):
        """기존 스킬의 SKILL.md 본문을 Modal로 편집합니다."""
        parts = _read_skill_md(name)
        if parts is None:
            await interaction.response.send_message(
                f"스킬 `{name}`을(를) 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        frontmatter_yaml, body = parts
        modal = SkillEditModal(name, frontmatter_yaml, body, self.bot)
        await interaction.response.send_modal(modal)

    @skills_edit.autocomplete("name")
    async def skills_edit_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=n, value=n)
            for n in _all_skill_names()
            if current.lower() in n.lower()
        ][:25]

    # ── /skills-generate ──────────────────────────────────────

    @app_commands.command(
        name="skills-generate",
        description="LLM으로 스킬 실행 절차를 자동 생성합니다 (관리자 전용)",
    )
    # @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        name="대상 스킬 이름",
        prompt="어떤 기능을 하는 스킬인지 자연어로 설명",
    )
    async def skills_generate(
        self,
        interaction: discord.Interaction,
        name: str,
        prompt: str,
    ):
        """LLM에게 스킬 설명을 주고 SKILL.md 본문을 자동 생성합니다."""
        parts = _read_skill_md(name)
        if parts is None:
            await interaction.response.send_message(
                f"스킬 `{name}`을(를) 찾을 수 없습니다.\n"
                "`/skills-create`로 먼저 스킬을 생성하세요.",
                ephemeral=True,
            )
            return

        frontmatter_yaml, current_body = parts
        fm = yaml.safe_load(frontmatter_yaml) or {}

        await interaction.response.defer(ephemeral=True)

        # LLM에게 SKILL.md 본문 생성 요청
        system_prompt = (
            "당신은 디스코드 봇 스킬 작성 전문가입니다.\n"
            "사용자의 설명을 바탕으로 SKILL.md 본문(마크다운)을 생성하세요.\n\n"
            "본문에는 반드시 다음 섹션을 포함하세요:\n"
            "## 트리거 조건\n"
            "## 의도 분류 (테이블 형식)\n"
            "## 실행 절차 (의도별 단계적 절차)\n"
            "## 출력 형식\n"
            "## 에러 처리\n\n"
            "실행 절차에서 명령어를 실행해야 하면 구체적인 명령어를 포함하세요.\n"
            "마크다운만 출력하세요. frontmatter(---)는 포함하지 마세요.\n\n"
            f"스킬 이름: {name}\n"
            f"설명: {fm.get('description', '')}\n"
            f"실행기: {fm.get('executor', '없음')}\n"
            f"트리거: {', '.join(fm.get('triggers', []))}\n"
        )

        response = await self.bot.llm_client.chat(
            prompt=prompt,
            system_prompt=system_prompt,
        )

        if not response.available or not response.content:
            await interaction.followup.send(
                "LLM 응답 생성에 실패했습니다.", ephemeral=True,
            )
            return

        generated_body = response.content.strip()

        # 미리보기 + 적용/편집/취소 선택
        preview = generated_body[:1500]
        if len(generated_body) > 1500:
            preview += "\n\n…(이하 생략)"

        embed = discord.Embed(
            title=f"🤖 `{name}` 본문 자동 생성 결과",
            description=f"```md\n{preview}\n```",
            color=discord.Color.purple(),
        )
        embed.set_footer(text=f"총 {len(generated_body)}자 생성됨")

        view = GenerateResultView(name, frontmatter_yaml, generated_body, self.bot)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @skills_generate.autocomplete("name")
    async def skills_generate_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=n, value=n)
            for n in _all_skill_names()
            if current.lower() in n.lower()
        ][:25]

    # ── /skills-info ──────────────────────────────────────────

    @app_commands.command(
        name="skills-info",
        description="스킬의 상세 정보를 확인합니다",
    )
    @app_commands.describe(name="조회할 스킬 이름")
    async def skills_info(self, interaction: discord.Interaction, name: str):
        """SKILL.md의 frontmatter + 본문 미리보기를 Embed로 표시합니다."""
        parts = _read_skill_md(name)
        if parts is None:
            await interaction.response.send_message(
                f"스킬 `{name}`을(를) 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        frontmatter_yaml, body = parts
        fm = yaml.safe_load(frontmatter_yaml) or {}

        embed = discord.Embed(
            title=f"📄 스킬: {fm.get('name', name)}",
            description=fm.get("description", "(설명 없음)"),
            color=discord.Color.blue(),
        )

        # 기본 정보
        triggers = fm.get("triggers", [])
        embed.add_field(
            name="트리거",
            value=", ".join(triggers[:10]) if triggers else "(없음)",
            inline=True,
        )
        embed.add_field(
            name="실행기",
            value=fm.get("executor", "없음"),
            inline=True,
        )
        embed.add_field(
            name="credentials",
            value=fm.get("credentials", "없음"),
            inline=True,
        )
        embed.add_field(
            name="사용자 실행 가능",
            value="예" if fm.get("user-invocable", True) else "아니오",
            inline=True,
        )

        # 활성 상태
        is_active = self.bot.skill_loader.get_skill(name) is not None
        is_unconfigured = name in self.bot.skill_loader.unconfigured_skills
        if is_active:
            status = "🟢 활성"
        elif is_unconfigured:
            status = "🟡 접속 정보 미설정"
        else:
            status = "⚪ 비활성"
        embed.add_field(name="상태", value=status, inline=True)

        # 본문 미리보기
        body_preview = body[:800] if body else "(본문 없음)"
        if len(body) > 800:
            body_preview += "\n…"
        embed.add_field(
            name="본문 미리보기",
            value=f"```md\n{body_preview}\n```",
            inline=False,
        )

        embed.set_footer(
            text=f"파일: skills/{name}/SKILL.md · 본문 {len(body)}자"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @skills_info.autocomplete("name")
    async def skills_info_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=n, value=n)
            for n in _all_skill_names()
            if current.lower() in n.lower()
        ][:25]

    # ── /skills-delete ────────────────────────────────────────

    @app_commands.command(
        name="skills-delete",
        description="스킬을 삭제합니다 (관리자 전용)",
    )
    # @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(name="삭제할 스킬 이름")
    async def skills_delete(self, interaction: discord.Interaction, name: str):
        """확인 버튼을 거쳐 스킬 디렉토리를 삭제합니다."""
        if _find_skill_md(name) is None:
            await interaction.response.send_message(
                f"스킬 `{name}`을(를) 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"🗑️ 스킬 `{name}` 삭제",
            description=(
                f"`skills/{name}/` 디렉토리가 완전히 삭제됩니다.\n"
                "이 작업은 되돌릴 수 없습니다."
            ),
            color=discord.Color.red(),
        )
        view = SkillDeleteConfirmView(name, self.bot)
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True,
        )

    @skills_delete.autocomplete("name")
    async def skills_delete_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=n, value=n)
            for n in _all_skill_names()
            if current.lower() in n.lower()
        ][:25]

    # ── /skills-reload ────────────────────────────────────────

    @app_commands.command(
        name="skills-reload",
        description="스킬을 다시 로드합니다 (관리자 전용)",
    )
    # @app_commands.checks.has_permissions(administrator=True)
    async def skills_reload(self, interaction: discord.Interaction):
        """credentials 파일 추가 후 봇 재시작 없이 스킬을 리로드합니다."""
        active, pending = _reload_skills(self.bot)

        msg = f"스킬 리로드 완료: {active}개 활성"
        if pending:
            msg += f", {pending}개 미설정"
        await interaction.response.send_message(msg, ephemeral=True)

    # ── 권한 검사 ─────────────────────────────────────────────

    def _check_permission(
        self,
        interaction: discord.Interaction,
        skill: Skill,
    ) -> bool:
        """스킬 실행 권한을 검사합니다."""
        allowed_roles = skill.config.get("allowed_roles", [])
        if not allowed_roles:
            return True

        if interaction.user.guild_permissions.administrator:
            return True

        user_role_names = [role.name for role in interaction.user.roles]
        return any(role in user_role_names for role in allowed_roles)


async def setup(bot: "GireyBot"):
    await bot.add_cog(SkillCommands(bot))
