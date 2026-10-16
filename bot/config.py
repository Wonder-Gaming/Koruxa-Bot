"""Configuration loaded from environment variables (populated from .env by Docker)."""

import os
from dataclasses import dataclass

from .crypto import validate_key


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str
    encryption_key: str
    guild_id: int | None
    log_level: str
    data_dir: str
    highscores_minutes: int
    highscores_demo: bool
    weekly_reset_day: int  # 0 = Monday … 6 = Sunday (UTC)
    weekly_reset_hour: int  # 0-23 (UTC)
    clan_tag: str  # fallback for bot.clan_tag when no clan token is loaded ("" = everyone)


def load_config() -> Config:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_TOKEN is not set in .env")

    encryption_key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not encryption_key:
        raise ConfigError("ENCRYPTION_KEY is not set in .env (see .env.example for how to generate one)")
    try:
        validate_key(encryption_key)
    except ValueError:
        raise ConfigError("ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64-encoded bytes)") from None

    guild_raw = os.environ.get("GUILD_ID", "").strip()
    try:
        guild_id = int(guild_raw) if guild_raw else None
    except ValueError:
        raise ConfigError("GUILD_ID must be a numeric Discord server ID") from None

    try:
        highscores_minutes = max(5, int(os.environ.get("HIGHSCORES_REFRESH_MINUTES", "").strip() or 15))
    except ValueError:
        raise ConfigError("HIGHSCORES_REFRESH_MINUTES must be a whole number of minutes") from None

    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_raw = os.environ.get("WEEKLY_RESET_DAY", "").strip().lower()[:3] or "mon"
    if day_raw not in days:
        raise ConfigError("WEEKLY_RESET_DAY must be a weekday name, e.g. Monday")
    try:
        reset_hour = int(os.environ.get("WEEKLY_RESET_HOUR", "").strip() or 0)
        if not 0 <= reset_hour <= 23:
            raise ValueError
    except ValueError:
        raise ConfigError("WEEKLY_RESET_HOUR must be an hour from 0 to 23 (UTC)") from None

    return Config(
        discord_token=token,
        encryption_key=encryption_key,
        guild_id=guild_id,
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        data_dir=os.environ.get("DATA_DIR", "/app/data"),
        highscores_minutes=highscores_minutes,
        weekly_reset_day=days.index(day_raw),
        weekly_reset_hour=reset_hour,
        clan_tag=os.environ.get("CLAN_TAG", "").strip().strip("[]"),
        highscores_demo=os.environ.get("HIGHSCORES_DEMO", "").strip().lower() in ("1", "true", "yes"),
    )
