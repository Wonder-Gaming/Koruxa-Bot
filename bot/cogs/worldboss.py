"""World boss tracker: polls the public /worldboss endpoint and posts alerts.

No event has run since the endpoint appeared, so its event shape is unknown
(the idle response is just {"event": null, "cache_seconds": 30, ...}). This
code therefore reads the event defensively: it looks for common field names for
the id, phase, boss and start/end times, shows every other field generically,
and logs the raw event when one first appears so the display can be refined.

Alerts (ping the World Boss role):
- an event starts
- a new phase opens
- daily strikes reset at 00:00 UTC while an event runs
An event ending is announced without a ping. Each server also gets a live
status message that is edited as the event progresses.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..alerts import post_alert, reset_status, update_status
from ..api import KoruxaError
from ..fmt import num, timestamp, title

if TYPE_CHECKING:
    from ..client import KoruxaBot

log = logging.getLogger(__name__)

FEATURE = "worldboss"
STATE_KEY = "worldboss"
POLL_SECONDS = 60
COLOUR = discord.Colour.purple()

ID_KEYS = ("id", "event_id", "key", "slug")
NAME_KEYS = ("name", "title", "event_name")
BOSS_KEYS = ("boss", "boss_name", "current_boss")
PHASE_KEYS = ("phase", "current_phase", "phase_number", "phase_index", "phase_no")
START_KEYS = ("starts_at", "start_at", "started_at", "start", "opens_at")
END_KEYS = ("ends_at", "end_at", "ended_at", "end", "closes_at")
ENDED_STATUSES = {"ended", "finished", "complete", "completed", "over", "closed", "expired"}


# ---------- reading the event ----------

def _first(event: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if event.get(k) not in (None, ""):
            return event[k]
    return None


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        value = _first(value, NAME_KEYS)
    return str(value) if isinstance(value, (str, int)) and value != "" else None


def event_key(event: dict[str, Any]) -> str:
    value = _first(event, ID_KEYS + START_KEYS + NAME_KEYS)
    return str(value) if value is not None else "event"


def event_phase(event: dict[str, Any]) -> int | None:
    value = _first(event, PHASE_KEYS)
    if isinstance(value, dict):
        value = _first(value, ("number", "index", "phase", "id"))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def event_stage(event: dict[str, Any], now: datetime) -> str:
    """'upcoming', 'running' or 'ended'."""
    status = str(event.get("status") or event.get("state") or "").lower()
    if status in ENDED_STATUSES:
        return "ended"
    for flag in ("active", "is_active", "running", "is_running"):
        if event.get(flag) is False:
            return "ended" if (ts := timestamp(_first(event, END_KEYS))) and ts <= now else "upcoming"
    start, end = timestamp(_first(event, START_KEYS)), timestamp(_first(event, END_KEYS))
    if end and end <= now:
        return "ended"
    if start and start > now:
        return "upcoming"
    return "running"


def _display(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return f"{value}%" if key.endswith(("pct", "percent")) else num(value)
    if isinstance(value, str) and (ts := timestamp(value)):
        return f"{discord.utils.format_dt(ts, 'R')} ({discord.utils.format_dt(ts, 'f')})"
    return str(value)[:200]


def _flatten(data: dict[str, Any], prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and depth < 1:
            items += _flatten(v, f"{key}.", depth + 1)
        elif isinstance(v, list):
            if v and all(not isinstance(x, (dict, list)) for x in v):
                items.append((key, ", ".join(map(str, v))))
            else:
                items.append((key, f"{len(v)} entries"))
        elif v is not None and not isinstance(v, dict):
            items.append((key, v))
    return items


def event_embed(event: dict[str, Any], generated_at: Any) -> discord.Embed:
    now = datetime.now(timezone.utc)
    stage = event_stage(event, now)
    name = _name(_first(event, NAME_KEYS)) or "World Boss"
    boss = _name(_first(event, BOSS_KEYS))
    phase = event_phase(event)

    lines = []
    if phase is not None:
        lines.append(f"**Phase {phase}**" + (f" · {boss}" if boss else ""))
    elif boss:
        lines.append(f"**{boss}**")
    if stage == "upcoming" and (start := timestamp(_first(event, START_KEYS))):
        lines.append(f"Opens {discord.utils.format_dt(start, 'R')}")
    elif stage == "running" and (end := timestamp(_first(event, END_KEYS))):
        lines.append(f"Ends {discord.utils.format_dt(end, 'R')}")
    elif stage == "ended":
        lines.append("Event over. Claim phase rewards on the World Boss page within 14 days.")

    embed = discord.Embed(
        title=f"🌀 {name}" + {"upcoming": " (coming soon)", "ended": " (ended)"}.get(stage, ""),
        description="\n".join(lines) or None,
        colour=COLOUR,
        timestamp=timestamp(generated_at),
    )
    shown = set(NAME_KEYS + BOSS_KEYS + PHASE_KEYS + ID_KEYS)
    for key, value in _flatten(event)[:20]:
        if key.split(".")[0] in shown:
            continue
        embed.add_field(name=title(key.replace(".", " · "))[:256], value=_display(key, value))
    embed.set_footer(text="Koruxa World Boss · updates every minute")
    return embed


def _embed_fingerprint(embed: discord.Embed) -> str:
    data = embed.to_dict()
    data.pop("timestamp", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


# ---------- the cog ----------

class WorldBoss(commands.Cog):
    def __init__(self, bot: KoruxaBot):
        self.bot = bot
        self._last_status: str | None = None
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()

    @tasks.loop(seconds=POLL_SECONDS)
    async def poll(self) -> None:
        try:
            data = await self.bot.api.get_public("worldboss", max_wait=30)
        except KoruxaError as e:
            log.warning("World boss poll failed: %s", e)
            return
        try:
            await self._process(data)
        except Exception:
            log.exception("World boss update failed")

    @poll.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _process(self, data: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        state = await self.bot.db.get_state(STATE_KEY) or {}
        event = data.get("event")

        if not isinstance(event, dict) or not event:
            if state.get("stage") in ("upcoming", "running"):
                await self._ended(state)
            if state:
                await self.bot.db.set_state(STATE_KEY, {})
            return

        key, stage, phase = event_key(event), event_stage(event, now), event_phase(event)
        embed = event_embed(event, data.get("generated_at"))
        new_event = state.get("event_key") != key

        if new_event or state.get("stage") != stage or state.get("phase") != phase:
            log.info("World boss event %s: stage=%s phase=%s raw=%s", key, stage, phase, json.dumps(event)[:4000])

        if new_event:
            await reset_status(self.bot, FEATURE)
            self._last_status = None
            state = {"event_key": key, "stage": None, "phase": phase, "reset_date": today}

        if stage == "upcoming":
            await self._post_status(embed)
        elif stage == "running" and state.get("stage") in (None, "upcoming"):
            sent = await post_alert(
                self.bot, FEATURE, embed=embed,
                content="🌀 **A World Boss event has started!** Go spend your strikes.",
            )
            await reset_status(self.bot, FEATURE, sent)  # the start alert becomes the live status message
            self._last_status = _embed_fingerprint(embed)
            state["reset_date"] = today
        elif stage == "running":
            old_phase = state.get("phase")
            if phase is not None and old_phase is not None and phase != old_phase:
                await post_alert(
                    self.bot, FEATURE,
                    content=f"🌀 **World Boss phase {old_phase} cleared!** Phase {phase} is open."
                    + (f" Boss: **{boss}**." if (boss := _name(_first(event, BOSS_KEYS))) else ""),
                )
            if state.get("reset_date") != today:
                await post_alert(
                    self.bot, FEATURE,
                    content="🗡️ **Daily strikes have reset.** Your 3 free World Boss strikes are ready.",
                )
                state["reset_date"] = today
            await self._post_status(embed)
        elif stage == "ended" and state.get("stage") in ("upcoming", "running"):
            await self._ended(state, embed)

        state["stage"], state["phase"] = stage, phase
        await self.bot.db.set_state(STATE_KEY, state)

    async def _post_status(self, embed: discord.Embed) -> None:
        fingerprint = _embed_fingerprint(embed)
        if fingerprint != self._last_status:
            await update_status(self.bot, FEATURE, embed)
            self._last_status = fingerprint

    async def _ended(self, state: dict[str, Any], embed: discord.Embed | None = None) -> None:
        log.info("World boss event %s ended", state.get("event_key"))
        final = embed or discord.Embed(
            title="🌀 World Boss (ended)",
            description="Event over. Claim phase rewards on the World Boss page within 14 days.",
            colour=COLOUR,
        )
        await update_status(self.bot, FEATURE, final)
        await post_alert(
            self.bot, FEATURE, ping=False,
            content="🌀 The World Boss event has ended. Claim your phase rewards on the World Boss page within 14 days.",
        )
        await reset_status(self.bot, FEATURE)
        self._last_status = None

    @app_commands.command(name="worldboss", description="Show the current World Boss event")
    @app_commands.describe(public="Show it to everyone in the channel (default: only you)")
    async def worldboss(self, interaction: discord.Interaction, public: bool = False) -> None:
        await interaction.response.defer(ephemeral=not public, thinking=True)
        try:
            data = await self.bot.api.get_public("worldboss", max_wait=10)
        except KoruxaError as e:
            log.warning("/worldboss failed: %s", e)
            await interaction.followup.send("Couldn't reach the Koruxa API right now. Try again shortly.")
            return
        event = data.get("event")
        if isinstance(event, dict) and event:
            await interaction.followup.send(embed=event_embed(event, data.get("generated_at")))
        else:
            await interaction.followup.send(
                "🌀 No World Boss event is running right now. I check every minute and post an alert when one starts."
            )


async def setup(bot: KoruxaBot) -> None:
    await bot.add_cog(WorldBoss(bot))
