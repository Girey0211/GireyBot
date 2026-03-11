"""
설정 파일 로더

로딩 순서:
1. config/default.yaml     — 동작 설정 (기본값)
2. config/secrets.yaml     — 민감 정보 + LLM 모델 설정 (deep merge)
3. config/guilds/{id}/config.yaml — 서버별 오버라이드 (deep merge)
"""

import copy
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("girey-bot.config")

# 프로젝트 루트 = config_loader.py의 상위(core/) 의 상위
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"


def deep_merge(base: dict, override: dict) -> dict:
    """
    두 dict를 deep merge합니다.

    병합 규칙 (project_plan.md 참조):
    - dict: 키 단위로 재귀 병합
    - list: 서버 값이 있으면 통째로 교체
    - scalar: 서버 값이 있으면 교체
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    """YAML 파일을 로드합니다. 없으면 빈 dict 반환."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_default_config() -> dict[str, Any]:
    """
    config/default.yaml + config/secrets.yaml 을 병합하여 반환합니다.

    default.yaml: 동작 설정 (provider 선택, auto_detect 등)
    secrets.yaml: 민감 정보 + 프로바이더별 모델/키 설정
    """
    default_path = CONFIG_DIR / "default.yaml"
    secrets_path = BASE_DIR / "secrets.yaml"

    config = _load_yaml(default_path)
    if config:
        logger.info(f"기본 설정 로드: {default_path}")
    else:
        logger.warning(f"기본 설정 파일을 찾을 수 없습니다: {default_path}")

    secrets = _load_yaml(secrets_path)
    if secrets:
        config = deep_merge(config, secrets)
        logger.info(f"시크릿 설정 병합: {secrets_path}")
    else:
        logger.warning(
            f"시크릿 설정 파일을 찾을 수 없습니다: {secrets_path}\n"
            f"  → secrets.example.yaml을 복사하여 secrets.yaml을 생성하세요."
        )

    return config


def load_guild_config(guild_id: int | str) -> dict[str, Any]:
    """
    서버별 설정을 로드하고 디폴트와 deep merge합니다.

    Args:
        guild_id: Discord Guild(서버) ID

    Returns:
        디폴트 + 시크릿 + 서버별 오버라이드가 병합된 최종 설정
    """
    base_config = load_default_config()

    guild_config_path = CONFIG_DIR / "guilds" / str(guild_id) / "config.yaml"
    if not guild_config_path.exists():
        logger.debug(f"서버별 설정 없음 (기본값 사용): guild_id={guild_id}")
        return base_config

    guild_override = _load_yaml(guild_config_path)
    merged = deep_merge(base_config, guild_override)
    logger.info(f"서버별 설정 병합 완료: guild_id={guild_id}")
    return merged


def get_bot_names(config: dict[str, Any] | None = None) -> list[str]:
    """
    봇 이름/별명 목록을 반환합니다.

    config의 bot.name 값을 사용합니다.
    """
    if config is None:
        config = load_default_config()

    bot_name = config.get("bot", {}).get("name", "기리봇")
    return [bot_name]


def get_discord_token(config: dict[str, Any] | None = None) -> str:
    """
    Discord 토큰을 config에서 가져옵니다.

    Raises:
        RuntimeError: 토큰이 설정되지 않았거나 형식이 올바르지 않을 때
    """
    if config is None:
        config = load_default_config()

    token = config.get("discord", {}).get("token")

    if not token:
        raise RuntimeError(
            "Discord 토큰이 설정되지 않았습니다.\n"
            "config/secrets.yaml의 discord.token에 토큰을 입력하세요.\n"
            "  → config/secrets.example.yaml을 참고하세요."
        )

    if "." not in token:
        raise RuntimeError(
            "discord.token 형식이 올바르지 않습니다.\n"
            "Discord Bot Token은 'MTAxNjU5ODk4OTE0MTYyMDgzNA.G1a2b3.XYZ...' 같은 형식입니다.\n"
            "Discord Developer Portal > Application > Bot > Reset Token 에서 복사하세요.\n"
            "※ Application ID, Client Secret, OAuth2 Secret 과 혼동하지 마세요."
        )

    return token
