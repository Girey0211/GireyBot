"""
로깅 설정 모듈

컬러 포매터 및 기본 로깅 핸들러를 구성합니다.
"""

import logging

logging.Formatter.default_msec_format = '%s.%03d'


class ColorFormatter(logging.Formatter):
    """레벨별 ANSI 색상을 적용하는 로그 포매터"""
    COLORS = {
        logging.DEBUG:    "\033[36m",   # cyan
        logging.INFO:     "\033[32m",   # green
        logging.WARNING:  "\033[33m",   # yellow
        logging.ERROR:    "\033[31m",   # red
        logging.CRITICAL: "\033[1;31m", # bold red
    }
    RESET = "\033[0m"

    def __init__(self, fmt: str):
        super().__init__(fmt)

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        record.name = f"\033[34m{record.name}{self.RESET}"  # blue
        return super().format(record)


def setup_logging(level: int = logging.INFO) -> None:
    """컬러 로깅 핸들러를 루트 로거에 등록합니다."""
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler])
