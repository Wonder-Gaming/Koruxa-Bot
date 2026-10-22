"""Clan highscores built from linked members' profiles (no clan token needed).

Every HIGHSCORES_REFRESH_MINUTES the bot fetches /me for each linked user, one
at a time, and saves their skill levels. That is one call per player per
refresh, against each player's own 240/hour token limit (4/hour at the
15-minute default). The API client also reuses a cached /me, e.g. from a
recent /profile.

Each server with a highscores channel gets one image per board (Overall, then
Combat, Gathering and Artisan, the long groups split over two boards so every
image is the same size; drawn by bot/render.py) showing the top linked members. They are edited in
place when the rankings change, and reposted in order if one goes missing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import io
import json
import logging
import random
import time
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from ..api import KoruxaError, TokenRevoked
from ..crypto import TokenDecryptError
from ..db import Snapshot
from ..fmt import compact, num, timestamp
from ..progress import gains
from ..render import BOARD_ROWS, Board, Row, render_board
from ..skills import SKILL_GROUPS, SKILL_SOURCE, skill_emoji, skill_name

if TYPE_CHECKING:
    from ..client import KoruxaBot

log = logging.getLogger(__name__)

FEATURE = "highscores"
GROUP_ORDER = ["Combat", "Gathering", "Artisan"]


def _board_parts(group: str) -> list[list[str]]:
    """A skill group's skills, split evenly into boards of at most BOARD_ROWS (Artisan's 11 -> 6 + 5)."""
    keys = next(k for name, _, k in SKILL_GROUPS if name == group)
    parts = -(-len(keys) // BOARD_ROWS)
    per = -(-len(keys) // parts)
    return [keys[i:i + per] for i in range(0, len(keys), per)]


# Message order in the channel; a group too long for one board becomes "Combat 1", "Combat 2", ...
BOARDS = ["Overall"] + [
    group if len(parts) == 1 else f"{group} {i}"
    for group in GROUP_ORDER
    for parts in [_board_parts(group)]
    for i in range(1, len(parts) + 1)
]
TOP_N = 3
# Accent colours, matching the section headers in the game's sidebar.
BOARD_COLOURS = {
    "Overall": (251, 191, 36),
    "Combat": (239, 68, 68),
    "Gathering": (34, 197, 94),
    "Artisan": (245, 158, 11),
}
REQUEST_GAP_SECONDS = 1.0  # spread the refresh out instead of bursting
GAINS_DAYS = 7  # the Overall board's "XP gained" window
EDIT_GAP_SECONDS = 2.0  # Discord rate-limits rapid edits with new images in one channel


DEMO_NAMES = ["Riftbreaker", "Mossy", "xX_Slayer_Xx", "Pudge", "TotoroHero", "Mayhem", "Grindset", "Lil Nugget"]


def demo_players() -> list[Snapshot]:
    """Made-up players to preview full boards (HIGHSCORES_DEMO=1). Never stored."""
    rng = random.Random(42)  # same fake data every time
    all_skills = [key for _, _, keys in SKILL_GROUPS for key in keys]
    players = []
    for i, name in enumerate(DEMO_NAMES):
        skills = {k: [(lvl := rng.randint(15, 125)), lvl * 10_000] for k in all_skills}
        players.append(Snapshot(
            -i - 1, -i - 1, name, "DEMO", skills, "",
            total_level=sum(lv for lv, _ in skills.values()),
            total_xp=sum(xp for _, xp in skills.values()),
            combat_level=rng.randint(40, 125),
        ))
    return players


def _top(entries: list[tuple[Snapshot, int, str]]) -> list[tuple[str, str]]:
    """entries: (player, sort value, value text) -> the top TOP_N as (name, value text)."""
    ranked = sorted(entries, key=lambda e: (-e[1], e[0].username.lower()))[:TOP_N]
    return [(p.username, shown) for p, _, shown in ranked]


def build_board(
    board: str,
    players: list[Snapshot],
    refresh_minutes: int,
    demo: bool = False,
    week_gains: dict[int, int] | None = None,
    clan: str = "",
) -> Board:
    """clan: who the boards count, e.g. "[MOSS]" ("" = every linked character)."""
    week_gains = week_gains or {}
    rows: list[Row] = []
    if board == "Overall":
        title, emoji, first_column = "Overall highscores", "🏆", "Stat"
        stats = [
            ("📊", "Total level", lambda p: p.total_level, num),
            ("⭐", "Total XP", lambda p: p.total_xp, compact),
            ("⚔️", "Combat level", lambda p: p.combat_level, lambda v: f"Lv {v}"),
            ("📈", f"XP gained {GAINS_DAYS}d", lambda p: week_gains.get(p.character_id), lambda v: "+" + compact(v)),
        ]
        for icon, label, value, shown in stats:
            entries = [(p, v, shown(v)) for p in players if isinstance(v := value(p), int) and v > 0]
            rows.append(Row(icon, label, _top(entries)))
    else:
        group, _, part = board.partition(" ")
        parts = _board_parts(group)
        label, emoji, _ = next(g for g in SKILL_GROUPS if g[0] == group)
        keys = parts[int(part) - 1] if part else parts[0]
        title, first_column = f"{label} highscores" + (f" ({part}/{len(parts)})" if part else ""), "Skill"
        for key in keys:
            source = SKILL_SOURCE.get(key, key)  # e.g. Alchemy shows the Magic level
            # Sort by level, then XP to break ties.
            entries = [
                (p, p.skills[source][0] * 10**12 + p.skills[source][1], f"Lv {p.skills[source][0]}")
                for p in players if source in p.skills
            ]
            rows.append(Row(skill_emoji(key), skill_name(key), _top(entries)))

    real = [p for p in players if p.character_id > 0]  # demo players have negative ids
    latest = max((ts for p in real if (ts := timestamp(p.updated_at))), default=None)
    members = f"linked {clan} member" if clan else "linked player"
    footer = f"{len(real)} {members}{'s' if len(real) != 1 else ''}"
    if latest:
        footer += f" · updated {latest:%d %b %Y %H:%M} UTC"
    if demo:
        footer += f" · plus {len(DEMO_NAMES)} demo players"
    return Board(
        title=title,
        emoji=emoji,
        accent=BOARD_COLOURS[board.partition(" ")[0]],
        subtitle=f"Top {TOP_N} {members}s · /link to join · updates every {refresh_minutes} min",
        first_column=first_column,
        rows=rows,
        footer=footer,
    )


def _fingerprint(board: Board) -> str:
    return hashlib.sha256(json.dumps(dataclasses.asdict(board), sort_keys=True).encode()).hexdigest()


class Highscores(commands.Cog):
    def __init__(self, bot: KoruxaBot):
        self.bot = bot
        self.refresh_seconds = bot.config.highscores_minutes * 60
        self._last_refresh = 0.0  # monotonic; 0 = refresh on the first tick
        self._rendered: dict[tuple[int, str], str] = {}
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    @tasks.loop(minutes=1)
    async def tick(self) -> None:
        try:
            if not (await self.bot.db.feature_targets(FEATURE) or await self.bot.db.feature_targets("feed")):
                return  # nothing uses the profile data: don't spend API calls
            if time.monotonic() - self._last_refresh >= self.refresh_seconds or not self._last_refresh:
                self._last_refresh = time.monotonic()
                await self.refresh_players()
            await self.render_all()
        except Exception:
            log.exception("Highscores update failed")

    @tick.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ---------- fetching ----------

    async def refresh_players(self) -> None:
        ok = failed = 0
        for link in await self.bot.db.list_links():
            try:
                token = self.bot.cipher.decrypt(link.token_enc)
            except TokenDecryptError:
                continue
            try:
                me = await self.bot.api.get(token, "me", max_wait=60)
            except TokenRevoked:
                await self._revoked(link, token)
                continue
            except KoruxaError as e:
                failed += 1
                log.warning("Highscores: /me failed for user %s character %s: %s", link.discord_id, link.character_id, e)
                continue
            await self.bot.db.save_snapshot(link.discord_id, me)
            ok += 1
            await asyncio.sleep(REQUEST_GAP_SECONDS)
        log.info("Highscores: refreshed %d character(s), %d failed", ok, failed)

    async def _revoked(self, link, token: str) -> None:
        await self.bot.db.delete_link(link.discord_id, link.character_id)
        self.bot.api.forget(token)
        log.info("Token for user %s character %s was rejected during refresh; unlinked", link.discord_id, link.character_id)
        try:
            user = await self.bot.fetch_user(link.discord_id)
            await user.send(
                f"🔒 Koruxa rejected the Player API token for **{link.username}**, so it was probably revoked. "
                "I've unlinked that character and removed it from the clan highscores.\n"
                "Create a new token in-game (**Settings > Account > API access**) and use **/link** to add it back."
            )
        except discord.HTTPException:
            log.info("Couldn't DM user %s about their revoked token", link.discord_id)

    # ---------- rendering ----------

    async def render_all(self) -> None:
        for cfg in await self.bot.db.feature_targets(FEATURE):
            channel = self.bot.get_channel(cfg.channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            players = await self.bot.clan_snapshots(channel.guild)
            week_gains = {}
            for p in players:
                if g := await gains(self.bot.db, p, GAINS_DAYS):
                    week_gains[p.character_id] = g.total_xp
            # Demo players only fill out a preview; once a server links a clan token its boards are members only.
            cog = self.bot.get_cog("Clan")
            linked = bool(cog and cog.clan_tag_for(channel.guild.id))  # type: ignore[attr-defined]
            demo = self.bot.config.highscores_demo and not linked
            if demo:
                extra = demo_players()
                players += extra
                week_gains.update({p.character_id: p.total_xp // (5 + i) for i, p in enumerate(extra)})
            tag = self.bot.clan_tag(channel.guild.id)
            clan = f"[{tag}]" if tag else ""
            boards = {
                board: build_board(board, players, self.bot.config.highscores_minutes, demo, week_gains, clan)
                for board in BOARDS
            }
            await self._publish(channel, boards)

    async def _publish(self, channel: discord.TextChannel, boards: dict[str, Board]) -> None:
        guild_id = channel.guild.id
        state_key = f"highscores:{guild_id}"
        state = await self.bot.db.get_state(state_key) or {}
        messages: dict[str, int] = state.get("messages", {})

        async def image(board: str) -> discord.File:
            png = await asyncio.to_thread(render_board, boards[board])
            return discord.File(io.BytesIO(png), filename=f"{board.lower()}-highscores.png")

        if state.get("channel_id") == channel.id and all(b in messages for b in BOARDS):
            intact = True
            for board in BOARDS:
                fp = _fingerprint(boards[board])
                if self._rendered.get((guild_id, board)) == fp:
                    continue
                try:
                    await channel.get_partial_message(messages[board]).edit(
                        content=None, embed=None, attachments=[await image(board)]
                    )
                    self._rendered[(guild_id, board)] = fp
                    await asyncio.sleep(EDIT_GAP_SECONDS)
                except discord.NotFound:
                    intact = False
                    break
                except discord.HTTPException as e:
                    log.warning("Couldn't edit %s highscores in guild %s: %s", board, guild_id, e)
            if intact:
                return

        # First post, new channel, a new board, or a message went missing: (re)post them all in order.
        old_channel = self.bot.get_channel(state.get("channel_id") or 0)
        if isinstance(old_channel, discord.TextChannel):
            for message_id in messages.values():
                try:
                    await old_channel.get_partial_message(message_id).delete()
                except discord.HTTPException:
                    pass
        new_messages = {}
        for board in BOARDS:
            try:
                message = await channel.send(file=await image(board), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as e:
                log.warning("Couldn't post highscores in guild %s: %s", guild_id, e)
                break
            new_messages[board] = message.id
            self._rendered[(guild_id, board)] = _fingerprint(boards[board])
        await self.bot.db.set_state(state_key, {"channel_id": channel.id, "messages": new_messages})
        log.info("Posted highscores in guild %s channel %s", guild_id, channel.id)


async def setup(bot: KoruxaBot) -> None:
    await bot.add_cog(Highscores(bot))
