"""
Discord Support Agent — 봇 엔트리포인트
"""

import logging
import os
from core.agent import GireyBot

# ─── 로깅 설정 ───────────────────────────────────────────────
logging.Formatter.default_msec_format = '%s.%03d'
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("girey-bot")

# ─── 실행 ────────────────────────────────────────────────────
def main():
    bot = GireyBot()
    try:
        bot.run(bot.discord_token, log_handler=None)
    except KeyboardInterrupt:
        pass
    finally:
        print("봇을 종료합니다.")
        os._exit(0)

if __name__ == "__main__":
    main()

