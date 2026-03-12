"""
Discord Support Agent — 봇 엔트리포인트
"""

import signal
import logging
from core.agent import GireyBot

# ─── 로깅 설정 ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("girey-bot")

# ─── 실행 ────────────────────────────────────────────────────
def main():
    bot = GireyBot()

    def _shutdown(_sig, _frame):
        logger.info("종료 시그널 수신, 봇을 종료합니다...")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        bot.run(bot.discord_token, log_handler=None)
    except SystemExit:
        pass

if __name__ == "__main__":
    main()
