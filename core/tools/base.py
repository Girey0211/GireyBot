"""
도구 베이스 클래스 — 모든 명령 실행기의 공통 인터페이스

스킬은 실행 방식(SSH/로컬/Docker)을 신경 쓰지 않고,
CommandExecutor.execute(command)만 호출하면 됩니다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CommandResult:
    """명령 실행 결과 (모든 실행기 공통)"""

    exit_code: int
    stdout: str
    stderr: str
    success: bool = True
    error: str | None = None
    executor_type: str = ""  # "local" | "ssh" | "docker"


class BaseExecutor(ABC):
    """명령 실행기 추상 베이스 클래스"""

    executor_type: str = "base"

    def __init__(self, allowed_commands: list[str] | None = None):
        self._allowed_commands = allowed_commands or []

    def is_allowed(self, command: str) -> bool:
        """명령이 화이트리스트에 있는지 확인합니다."""
        if not self._allowed_commands:
            return True  # 화이트리스트 미설정 시 전체 허용
        return any(command.startswith(prefix) for prefix in self._allowed_commands)

    def _blocked_result(self, command: str) -> CommandResult:
        return CommandResult(
            exit_code=-1,
            stdout="",
            stderr="",
            success=False,
            error=f"허용되지 않은 명령입니다: {command}",
            executor_type=self.executor_type,
        )

    @abstractmethod
    async def execute(self, command: str, timeout: int = 30) -> CommandResult:
        """명령을 실행하고 결과를 반환합니다."""
        ...

    @abstractmethod
    async def check_connection(self) -> bool:
        """실행 환경이 사용 가능한지 확인합니다."""
        ...
