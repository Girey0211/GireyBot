"""
Docker 명령 실행기

컨테이너 기반 서비스를 관리할 때 사용합니다.
docker CLI를 래핑하여 컨테이너 시작/종료/상태 확인/로그 조회를 수행합니다.
"""

import logging

from core.tools.local import LocalExecutor
from core.tools.base import CommandResult

logger = logging.getLogger("girey-bot.tools.docker")


class DockerExecutor(LocalExecutor):
    """
    Docker CLI 기반 컨테이너 관리 실행기.

    LocalExecutor를 상속하여 docker 명령만 허용합니다.

    사용 예:
        executor = DockerExecutor()
        result = await executor.execute("docker ps")
        result = await executor.start("minecraft-server")
    """

    executor_type = "docker"

    # docker 명령만 허용
    DEFAULT_ALLOWED = [
        "docker ps",
        "docker start",
        "docker stop",
        "docker restart",
        "docker logs",
        "docker inspect",
        "docker stats",
        "docker compose up",
        "docker compose down",
        "docker compose ps",
        "docker compose logs",
        "docker compose restart",
    ]

    def __init__(
        self,
        allowed_commands: list[str] | None = None,
        working_dir: str | None = None,
    ):
        super().__init__(
            allowed_commands=allowed_commands or self.DEFAULT_ALLOWED,
            working_dir=working_dir,
        )

    # ─── 편의 메서드 ──────────────

    async def ps(self, all_containers: bool = False) -> CommandResult:
        """실행 중인 컨테이너 목록"""
        cmd = "docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'"
        if all_containers:
            cmd = "docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'"
        return await self.execute(cmd)

    async def start(self, container: str) -> CommandResult:
        return await self.execute(f"docker start {container}")

    async def stop(self, container: str) -> CommandResult:
        return await self.execute(f"docker stop {container}")

    async def restart(self, container: str) -> CommandResult:
        return await self.execute(f"docker restart {container}")

    async def logs(self, container: str, tail: int = 50) -> CommandResult:
        return await self.execute(f"docker logs --tail {tail} {container}")

    async def status(self, container: str) -> CommandResult:
        """컨테이너 상태를 확인합니다."""
        return await self.execute(
            f"docker inspect --format '{{{{.State.Status}}}}' {container}"
        )

    async def compose_up(self, service: str | None = None) -> CommandResult:
        cmd = "docker compose up -d"
        if service:
            cmd += f" {service}"
        return await self.execute(cmd)

    async def compose_down(self, service: str | None = None) -> CommandResult:
        cmd = "docker compose down"
        if service:
            cmd += f" {service}"
        return await self.execute(cmd)

    async def check_connection(self) -> bool:
        result = await self.execute("docker ps -q")
        return result.success and result.exit_code == 0
