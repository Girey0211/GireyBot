FROM python:3.10-slim

WORKDIR /app

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 의존성 먼저 설치 (캐시 레이어 활용)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 소스 복사
COPY bot.py ./
COPY core/ ./core/
COPY config/ ./config/
COPY skills/ ./skills/
COPY data/persona.md ./data/persona.md

CMD ["uv", "run", "python", "bot.py"]
