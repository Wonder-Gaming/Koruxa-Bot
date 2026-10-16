FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 1000 bot && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin bot

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY assets ./assets
COPY bot ./bot

USER bot
CMD ["python", "-m", "bot"]
