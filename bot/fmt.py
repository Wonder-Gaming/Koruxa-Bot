"""Small formatting helpers shared by the cogs."""

from datetime import datetime
from typing import Any

import discord

DEFAULT_COLOUR = discord.Colour.orange()


def colour(hex_value: Any) -> discord.Colour:
    try:
        return discord.Colour(int(str(hex_value).lstrip("#"), 16))
    except (TypeError, ValueError):
        return DEFAULT_COLOUR


def num(value: Any) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "?"


def compact(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "?"
    for size, suffix in ((1e15, "Q"), (1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f"{value / size:.1f}{suffix}"
    return str(value)


def timestamp(value: Any) -> datetime | None:
    """Parse the API's ISO 8601 timestamps ("2026-10-15T08:46:42Z")."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def title(key: Any) -> str:
    return str(key or "?").replace("_", " ").title()


def bar(fraction: float, width: int = 12) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "▰" * filled + "▱" * (width - filled)
