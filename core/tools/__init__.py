"""
도구 모듈 — 스킬에서 사용하는 명령 실행기

공통 인터페이스(BaseExecutor)를 통해 실행 방식(SSH/로컬/Docker)에
관계없이 동일한 방법으로 명령을 실행할 수 있습니다.
"""

from core.tools.base import BaseExecutor, CommandResult
from core.tools.ssh import SSHExecutor
from core.tools.local import LocalExecutor
from core.tools.docker import DockerExecutor

__all__ = [
    "BaseExecutor",
    "CommandResult",
    "SSHExecutor",
    "LocalExecutor",
    "DockerExecutor",
]
