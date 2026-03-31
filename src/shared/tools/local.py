"""
로컬 명령 실행기

봇과 같은 머신에서 서비스를 제어하거나 스크립트를 실행할 때 사용합니다.
asyncio.subprocess를 통해 비동기로 프로세스를 실행합니다.
"""

import asyncio
import logging

from src.shared.tools.base import BaseExecutor, CommandResult

logger = logging.getLogger("girey-bot.tools.local")


class LocalExecutor(BaseExecutor):
    """
    로컬 subprocess 기반 명령 실행기.

    사용 예:
        executor = LocalExecutor(
            allowed_commands=["systemctl", "docker"],
            working_dir="/opt/services",
        )
        result = await executor.execute("systemctl status nginx")
    """

    executor_type = "local"

    def __init__(
        self,
        allowed_commands: list[str] | None = None,
        working_dir: str | None = None,
        shell: bool = True,
    ):
        super().__init__(allowed_commands)
        self.working_dir = working_dir
        self.shell = shell

    async def execute(self, command: str, timeout: int = 30) -> CommandResult:
        if not self.is_allowed(command):
            logger.warning(f"차단된 로컬 명령: {command}")
            return self._blocked_result(command)

        try:
            if self.shell:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.working_dir,
                )
            else:
                parts = command.split()
                proc = await asyncio.create_subprocess_exec(
                    *parts,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.working_dir,
                )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            return CommandResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace").strip(),
                stderr=stderr.decode("utf-8", errors="replace").strip(),
                executor_type=self.executor_type,
            )

        except asyncio.TimeoutError:
            logger.error(f"로컬 명령 타임아웃 ({timeout}s): {command}")
            try:
                proc.kill()
            except Exception:
                pass
            return CommandResult(
                exit_code=-1, stdout="", stderr="",
                success=False, error=f"타임아웃 ({timeout}초)",
                executor_type=self.executor_type,
            )
        except Exception as e:
            logger.error(f"로컬 명령 실행 실패: {e}")
            return CommandResult(
                exit_code=-1, stdout="", stderr="",
                success=False, error=str(e),
                executor_type=self.executor_type,
            )

    async def check_connection(self) -> bool:
        result = await self.execute("echo ok")
        return result.success and result.exit_code == 0
