"""
Discord Support Agent — 봇 엔트리포인트
"""

import logging
import os

from src.shared.logging import setup_logging
from src.shared.config import load_default_config
from src.main.agent import GireyBot

_config = load_default_config()
_log_level_str = _config.get("bot", {}).get("log_level", "INFO").upper()
_log_level = getattr(logging, _log_level_str, logging.INFO)
setup_logging(level=_log_level)
logger = logging.getLogger("girey-bot")


def main():
    bot = GireyBot()
    try:
        bot.run(bot.discord_token, log_handler=None)
    except KeyboardInterrupt:
        pass
    logger.info(f"봇을 종료합니다.")
    os._exit(0)

if __name__ == "__main__":
    main()
