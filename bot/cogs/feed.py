"""Clan feed and XP gains.

- Level-ups: every minute, linked players' saved levels are compared with the
  levels already announced. New ones are added to the running level-up post,
  edited in place, while it is still the newest message in the feed channel,
  from the same UTC day, and has room (MAX_FEED_ROWS, 7). Otherwise (another
  announcement went out, someone posted, or a new day) a new post starts, and
  rows that don't fit continue in further posts. A player's first snapshot only sets the baseline, so linking
  doesn't flood the feed.
- Weekly top gainers: posted to the feed channel on Mondays (UTC).
- /gains and /topgains: XP gained over a day, week or month, from the daily
  XP history saved with each snapshot.

All of it reuses the profile data the highscores refresh already fetches, so
it costs no extra API calls.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..alerts import post_alert
from ..db import Snapshot
from ..fmt import compact, num
from ..progress import Gains, gains
from ..render import MAX_FEED_ROWS, Gainer, LevelUp, render_level_ups, render_top_gainers
from ..skills import skill_emoji, skill_label, skill_name

if TYPE_CHECKING:
    from ..client import KoruxaBot
    from .account import Account

log = logging.getLogger(__name__)

FEATURE = "feed"
COLOUR = discord.Colour(0x22C55E)
INDENT = "​ "  # see highscores.py
MILESTONES = {99} | set(range(50, 201, 10))
RANKS = ["🥇", "🥈", "🥉", "`4.`", "`5.`", "`6.`", "`7.`", "`8.`", "`9.`", "`10.`"]
HISTORY_KEEP_DAYS = 400

PERIODS = [
    app_commands.Choice(name="Today (24 hours)", value=1),
    app_commands.Choice(name="This week (7 days)", value=7),
    app_commands.Choice(name="This month (30 days)", value=30),
]
PERIOD_NAMES = {1: "24 hours", 7: "7 days", 30: "30 days"}


def gainers_lines(ranked: list[tuple[Snapshot, Gains]]) -> str:
    lines = []
    for i, (snap, g) in enumerate(ranked):
        best = max(g.skills.items(), key=lambda kv: kv[1][0])
        levels = f" · {g.levels} level{'s' if g.levels != 1 else ''}" if g.levels else ""
        lines.append(
            f"{RANKS[i]} **{discord.utils.escape_markdown(snap.username)}** · +{compact(g.total_xp)} XP{levels}\n"
            f"{INDENT}Top skill: {skill_label(best[0])} +{compact(best[1][0])}"
        )
    return "\n".join(lines)


def gainer_rows(ranked: list[tuple[Snapshot, Gains]]) -> list[Gainer]:
    rows = []
    for snap, g in ranked:
        key, (xp, _) = max(g.skills.items(), key=lambda kv: kv[1][0])
        rows.append(Gainer(snap.username, f"+{compact(g.total_xp)}", g.levels, skill_emoji(key), skill_name(key), f"+{compact(xp)}"))
    return rows


def merge_level_ups(current: list[LevelUp], new: list[LevelUp]) -> list[LevelUp]:
    """Add `new` to the rows already shown. The same player and skill again becomes one row (98 -> 100,
    keeping the first "from" level), and each player's rows stay together for the shaded bands."""
    rows = list(current)
    for item in new:
        same = next((i for i, r in enumerate(rows) if r.player == item.player and r.skill == item.skill), None)
        if same is None:
            rows.append(item)
        else:
            old = rows[same].old
            rows[same] = dataclasses.replace(item, old=old,
                                             milestone=any(old < m <= item.new for m in MILESTONES))
    order = {}
    for r in rows:
        order.setdefault(r.player, len(order))
    return sorted(rows, key=lambda r: order[r.player])  # stable: keeps each player's rows in order


class Feed(commands.Cog):
    def __init__(self, bot: KoruxaBot):
        self.bot = bot
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    @tasks.loop(minutes=1)
    async def tick(self) -> None:
        try:
            await self.announce_level_ups()
            await self.weekly_recap()
        except Exception:
            log.exception("Clan feed update failed")

    @tick.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ---------- shared ----------

    async def members(self, guild: discord.Guild) -> list[Snapshot]:
        """Clan characters linked by members of `guild`."""
        return await self.bot.clan_snapshots(guild)

    async def top_gainers(self, guild: discord.Guild, days: int, limit: int) -> list[tuple[Snapshot, Gains]]:
        ranked = []
        for snap in await self.members(guild):
            g = await gains(self.bot.db, snap, days)
            if g and g.total_xp > 0:
                ranked.append((snap, g))
        ranked.sort(key=lambda sg: -sg[1].total_xp)
        return ranked[:limit]

    # ---------- level-ups ----------

    async def announce_level_ups(self) -> None:
        snapshots = await self.bot.db.list_snapshots()
        announced = await self.bot.db.get_announced()
        level_ups: dict[int, list[tuple[str, int, int]]] = {}
        for snap in snapshots:
            current = {key: level for key, (level, _) in snap.skills.items()}
            before = announced.get(snap.character_id)
            if before is not None:
                changed = [(k, before[k], lv) for k, lv in current.items() if k in before and lv > before[k]]
                if changed:
                    level_ups[snap.character_id] = changed
            if current != before:
                await self.bot.db.set_announced(snap.character_id, current)
        if not level_ups:
            return

        names = {s.character_id: s.username for s in snapshots}
        log.info("Level-ups: %s", {uid: [(k, n) for k, _, n in ch] for uid, ch in level_ups.items()})
        for cfg in await self.bot.db.feature_targets(FEATURE):
            guild = self.bot.get_guild(cfg.guild_id)
            if guild is None:
                continue
            in_clan = {s.character_id for s in await self.members(guild)}
            items = [
                LevelUp(names[cid], skill_emoji(key), skill_name(key), old, new, any(old < m <= new for m in MILESTONES))
                for cid, changed in level_ups.items()
                if cid in in_clan
                for key, old, new in changed
            ]
            if items:
                await self.post_level_ups(cfg, items)

    async def post_level_ups(self, cfg, items: list[LevelUp]) -> None:
        """Add level-ups to this server's running post, or start a new one (see the module docstring). Rows
        past MAX_FEED_ROWS continue in new posts, so every card stays one column."""
        channel = self.bot.get_channel(cfg.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        now = datetime.now(timezone.utc)
        state = await self.bot.db.get_state("feed_levelups") or {}
        post = state.get(str(cfg.guild_id)) or {}
        open_post = bool(post.get("message_id") and post.get("channel_id") == channel.id
                         and channel.last_message_id == post["message_id"]
                         and post.get("day") == now.date().isoformat())
        rows = merge_level_ups([LevelUp(**i) for i in post.get("items") or []] if open_post else [], items)
        chunks = [rows[i:i + MAX_FEED_ROWS] for i in range(0, len(rows), MAX_FEED_ROWS)]
        footer = f"{now:%d %b %Y} · last update {now:%H:%M} UTC"
        tag = self.bot.clan_tag(cfg.guild_id)
        subtitle = f"New levels from linked [{tag}] members" if tag else "New levels from linked players"

        for n, chunk in enumerate(chunks):
            png = await asyncio.to_thread(render_level_ups, chunk, footer, subtitle)
            message = None
            if n == 0 and open_post:
                try:
                    message = await channel.get_partial_message(post["message_id"]).edit(
                        attachments=[discord.File(io.BytesIO(png), filename="level-ups.png")])
                    log.info("Level-ups edited in guild %s (%d rows)", cfg.guild_id, len(chunk))
                except discord.NotFound:
                    message = None  # deleted: post it again below
                except discord.HTTPException as e:
                    log.warning("Couldn't edit the level-up post in guild %s: %s", cfg.guild_id, e)
                    return
            if message is None:
                sent = await post_alert(self.bot, FEATURE, image=png, filename="level-ups.png", ping=False,
                                        guild_id=cfg.guild_id)
                message = sent.get(cfg.guild_id)
                if message is None:
                    return
                log.info("Level-ups posted in guild %s (%d rows)", cfg.guild_id, len(chunk))
            state[str(cfg.guild_id)] = {
                "message_id": message.id, "channel_id": channel.id, "day": now.date().isoformat(),
                "items": [dataclasses.asdict(i) for i in chunk],
            }
            await self.bot.db.set_state("feed_levelups", state)

    # ---------- weekly recap ----------

    def last_weekly_reset(self, now: datetime) -> datetime:
        cfg = self.bot.config
        reset = now.replace(hour=cfg.weekly_reset_hour, minute=0, second=0, microsecond=0)
        reset -= timedelta(days=(now.weekday() - cfg.weekly_reset_day) % 7)
        return reset if reset <= now else reset - timedelta(days=7)

    async def weekly_recap(self) -> None:
        """Post last week's top gainers once, right after the weekly reset."""
        now = datetime.now(timezone.utc)
        reset = self.last_weekly_reset(now)
        state = await self.bot.db.get_state("weekly_recap") or {}
        if state.get("reset") == reset.isoformat():
            return
        await self.bot.db.set_state("weekly_recap", {"reset": reset.isoformat()})
        if not state or now - reset > timedelta(hours=6):
            # First start, or the bot was down over the reset: wait for the next one
            # rather than posting a stale recap.
            log.info("Weekly recap: skipping the %s reset (first start or too late)", reset.isoformat())
            return
        if not await self.bot.db.feature_targets(FEATURE):
            return
        await self.bot.db.prune_history((now.date() - timedelta(days=HISTORY_KEEP_DAYS)).isoformat())

        # Fresh numbers as of the reset, then compare against the end of the day before the week began.
        highscores = self.bot.get_cog("Highscores")
        if highscores is not None:
            await highscores.refresh_players()  # type: ignore[attr-defined]
        week_start = reset - timedelta(days=7)
        days_back = (now.date() - week_start.date()).days + 1
        week_end = reset - timedelta(seconds=1)

        for cfg in await self.bot.db.feature_targets(FEATURE):
            guild = self.bot.get_guild(cfg.guild_id)
            if guild is None:
                continue
            ranked = await self.top_gainers(guild, days_back, 5)
            if not ranked:
                continue
            png = await asyncio.to_thread(
                render_top_gainers,
                gainer_rows(ranked),
                "Weekly top gainers",
                f"XP gained {week_start:%a %d %b} – {week_end:%a %d %b} · /gains for your own",
                f"Week ended {reset:%d %b %Y %H:%M} UTC",
            )
            await post_alert(self.bot, FEATURE, image=png, filename="top-gainers.png", ping=False, guild_id=cfg.guild_id)
            log.info("Posted weekly recap in guild %s", cfg.guild_id)

    # ---------- commands ----------

    async def gains_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Your own characters first, then clan characters of other server members."""
        own = await self.bot.db.user_links(interaction.user.id)
        choices = [app_commands.Choice(name=f"{l.username} (yours)", value=str(l.character_id)) for l in own]
        if interaction.guild is not None:
            own_ids = {l.character_id for l in own}
            for snap in sorted(await self.members(interaction.guild), key=lambda s: s.username.lower()):
                if snap.character_id not in own_ids:
                    choices.append(app_commands.Choice(name=snap.username, value=str(snap.character_id)))
        return [c for c in choices if current.lower() in c.name.lower()][:25]

    @app_commands.command(name="gains", description="XP gained over the last day, week or month")
    @app_commands.describe(
        period="How far back (default: this week)",
        character="Which character (default: your first linked one); clanmates' characters are listed too",
        public="Show it to everyone in the channel (default: only you)",
    )
    @app_commands.choices(period=PERIODS)
    @app_commands.autocomplete(character=gains_autocomplete)
    async def gains_command(
        self,
        interaction: discord.Interaction,
        period: app_commands.Choice[int] | None = None,
        character: str | None = None,
        public: bool = False,
    ) -> None:
        days = period.value if period else 7
        own = await self.bot.db.user_links(interaction.user.id)
        if character is None or any(str(l.character_id) == character for l in own):
            # One of yours: refresh first (served from cache if fetched recently).
            account: Account | None = self.bot.get_cog("Account")  # type: ignore[assignment]
            linked = await account.resolve(interaction, character) if account else None
            if linked is None:
                return
            await interaction.response.defer(ephemeral=not public, thinking=True)
            if await account.fetch_me(interaction, linked) is None:
                return
            character_id = linked.link.character_id
        else:
            await interaction.response.defer(ephemeral=not public, thinking=True)
            character_id = int(character) if character.isdigit() else -1

        snap = await self.bot.db.get_snapshot(character_id)
        if snap is None:
            await interaction.followup.send("That character isn't linked. Pick one from the list.")
            return
        g = await gains(self.bot.db, snap, days)
        if g is None:
            await interaction.followup.send(
                f"No gains to show yet for **{discord.utils.escape_markdown(snap.username)}**. "
                "XP history is saved daily, so check back tomorrow."
            )
            return

        since = datetime.fromisoformat(g.since_day).replace(tzinfo=timezone.utc)
        lines = [
            f"{skill_label(k)} · **+{compact(xp)}** XP" + (f" · +{lv} lvl" if lv else "")
            for k, (xp, lv) in sorted(g.skills.items(), key=lambda kv: -kv[1][0])
        ]
        embed = discord.Embed(
            title=f"📈 {snap.username}: XP gained, last {PERIOD_NAMES[days]}",
            description=(
                f"**+{num(g.total_xp)} XP** · {g.levels} level{'s' if g.levels != 1 else ''}\n"
                f"Since {discord.utils.format_dt(since, 'D')}"
                + (" *(tracking started then)*" if g.partial else "")
                + "\n\n"
                + ("\n".join(lines) or "No XP gained in this period.")
            )[:4096],
            colour=COLOUR,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="topgains", description="The server's top XP gainers")
    @app_commands.describe(period="How far back (default: this week)", public="Show it to everyone (default: only you)")
    @app_commands.choices(period=PERIODS)
    @app_commands.guild_only()
    async def topgains(
        self,
        interaction: discord.Interaction,
        period: app_commands.Choice[int] | None = None,
        public: bool = False,
    ) -> None:
        assert interaction.guild is not None
        days = period.value if period else 7
        await interaction.response.defer(ephemeral=not public, thinking=True)
        ranked = await self.top_gainers(interaction.guild, days, 10)
        if not ranked:
            await interaction.followup.send("No gains to show yet. XP history is saved daily, so check back tomorrow.")
            return
        embed = discord.Embed(
            title=f"🏆 Top XP gainers, last {PERIOD_NAMES[days]}",
            description=gainers_lines(ranked),
            colour=discord.Colour(0xFBBF24),
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: KoruxaBot) -> None:
    await bot.add_cog(Feed(bot))
