"""
SSH 원격 명령 실행기

스킬(예: minecraft-server)에서 원격 서버를 제어할 때 사용합니다.
asyncssh를 통해 비동기로 SSH 접속 및 명령 실행을 수행합니다.
"""

import logging

from src.shared.tools.base import BaseExecutor, CommandResult

logger = logging.getLogger("girey-bot.tools.ssh")


class SSHExecutor(BaseExecutor):
    """
    asyncssh 기반 SSH 원격 명령 실행기.

    사용 예:
        executor = SSHExecutor(
            host="192.168.1.100", username="mc-admin",
            ssh_key_path="/path/to/key",
            allowed_commands=["systemctl", "uptime"],
        )
        result = await executor.execute("systemctl is-active minecraft.service")
    """

    executor_type = "ssh"

    # 기본 허용 명령 패턴
    DEFAULT_ALLOWED = [
        "systemctl is-active",
        "systemctl start",
        "systemctl stop",
        "systemctl status",
        "systemctl restart",
        "ps aux",
        "uptime",
        "free -h",
        "df -h",
        "cat /proc/uptime",
    ]

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: str | None = None,
        ssh_key_path: str | None = None,
        allowed_commands: list[str] | None = None,
    ):
        super().__init__(allowed_commands or self.DEFAULT_ALLOWED)
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssh_key_path = ssh_key_path

    async def execute(self, command: str, timeout: int = 30) -> CommandResult:
        if not self.is_allowed(command):
            logger.warning(f"차단된 SSH 명령: {command}")
            return self._blocked_result(command)

        try:
            import asyncssh

            connect_kwargs = {
                "host": self.host,
                "port": self.port,
                "username": self.username,
                "known_hosts": None,
            }

            if self.ssh_key_path:
                connect_kwargs["client_keys"] = [self.ssh_key_path]
            elif self.password:
                connect_kwargs["password"] = self.password

            async with asyncssh.connect(**connect_kwargs) as conn:
                result = await conn.run(command, timeout=timeout)

                return CommandResult(
                    exit_code=result.exit_status or 0,
                    stdout=(result.stdout or "").strip(),
                    stderr=(result.stderr or "").strip(),
                    executor_type=self.executor_type,
                )

        except ImportError:
            logger.error("asyncssh 미설치 — 'uv add asyncssh'")
            return CommandResult(
                exit_code=-1, stdout="", stderr="",
                success=False, error="asyncssh 미설치",
                executor_type=self.executor_type,
            )
        except Exception as e:
            logger.error(f"SSH 실행 실패 [{self.host}]: {e}")
            return CommandResult(
                exit_code=-1, stdout="", stderr="",
                success=False, error=str(e),
                executor_type=self.executor_type,
            )

    async def check_connection(self) -> bool:
        result = await self.execute("uptime")
        return result.success and result.exit_code == 0
