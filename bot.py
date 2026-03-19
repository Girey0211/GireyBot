"""
Discord Support Agent — 봇 엔트리포인트
"""

import logging
import os

from core.logging import setup_logging
from core.agent import GireyBot

setup_logging()
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
