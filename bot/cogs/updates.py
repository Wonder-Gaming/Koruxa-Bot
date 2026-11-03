"""Patch notes: when the bot starts on a new version, post that version's CHANGELOG.md section.

The version is the newest "## [x.y.z]" heading in CHANGELOG.md. On startup it is compared with the last one
announced (bot_state "patch_notes"); a new one is posted to each server's Bot Updates channel, or its Help
Menu channel if it has none, and deleted again after POST_HOURS. Deletion is tracked in bot_state, so it
still happens after a restart. The very first run only records the current version, so installing the bot
doesn't post old notes.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from ..client import KoruxaBot

log = logging.getLogger(__name__)

CHANGELOG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "CHANGELOG.md")
POST_HOURS = 12
HEADING = re.compile(r"^## \[([^\]]+)\](?:\s*-\s*(\S+))?\s*$", re.M)


def latest_release(path: str = CHANGELOG) -> tuple[str, str, str] | None:
    """(version, date, notes) of the newest CHANGELOG.md section, or None without a changelog."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    heads = list(HEADING.finditer(text))
    if not heads:
        return None
    first = heads[0]
    end = heads[1].start() if len(heads) > 1 else len(text)
    return first.group(1), first.group(2) or "", text[first.end():end].strip()


def notes_embed(version: str, date: str, notes: str, delete_at: datetime) -> discord.Embed:
    # Discord markdown has no ### headings in embeds; make them bold lines instead.
    body = re.sub(r"^#{3,}\s*(.+)$", r"**\1**", notes, flags=re.M)
    if len(body) > 4000:
        body = body[:3990].rsplit("\n", 1)[0] + "\n…"
    embed = discord.Embed(title=f"📰 Koruxa Bot updated to v{version}", description=body,
                          colour=discord.Colour(0x60A5FA))
    embed.set_footer(text=(f"Released {date} · " if date else "") + f"this message deletes itself after {POST_HOURS} hours")
    return embed


class Updates(commands.Cog):
    def __init__(self, bot: KoruxaBot):
        self.bot = bot
        self._announced = False

    async def cog_load(self) -> None:
        self.cleanup.start()

    async def cog_unload(self) -> None:
        self.cleanup.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._announced:  # on_ready also fires after reconnects
            self._announced = True
            await self.announce()

    async def channels(self) -> list[discord.TextChannel]:
        """Each server's Bot Updates channel, else its Help Menu channel."""
        picked: dict[int, int] = {}
        for feature in ("help", "updates"):  # updates last, so it wins
            for cfg in await self.bot.db.feature_targets(feature):
                picked[cfg.guild_id] = cfg.channel_id
        return [c for cid in picked.values() if isinstance(c := self.bot.get_channel(cid), discord.TextChannel)]

    async def announce(self) -> None:
        release = latest_release()
        if release is None:
            return
        version, date, notes = release
        state = await self.bot.db.get_state("patch_notes") or {}
        if state.get("version") == version:
            return
        posts = state.get("posts") or []
        if state.get("version") is None:
            log.info("Patch notes: first run, recording v%s without posting", version)
        else:
            delete_at = datetime.now(timezone.utc) + timedelta(hours=POST_HOURS)
            embed = notes_embed(version, date, notes, delete_at)
            for channel in await self.channels():
                try:
                    message = await channel.send(
                        content=f"Deletes {discord.utils.format_dt(delete_at, 'R')}",
                        embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    posts.append({"channel_id": channel.id, "message_id": message.id,
                                  "delete_at": delete_at.isoformat(timespec="seconds")})
                except discord.HTTPException as e:
                    log.warning("Couldn't post patch notes in guild %s: %s", channel.guild.id, e)
            log.info("Posted v%s patch notes to %d server(s)", version, len(posts))
        await self.bot.db.set_state("patch_notes", {"version": version, "posts": posts})

    @tasks.loop(minutes=10)
    async def cleanup(self) -> None:
        state = await self.bot.db.get_state("patch_notes") or {}
        posts = state.get("posts") or []
        now = datetime.now(timezone.utc)
        keep = []
        for post in posts:
            if datetime.fromisoformat(post["delete_at"]) > now:
                keep.append(post)
                continue
            channel = self.bot.get_channel(post["channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.get_partial_message(post["message_id"]).delete()
                except discord.NotFound:
                    pass  # already deleted by someone
                except discord.HTTPException as e:
                    log.warning("Couldn't delete patch notes %s: %s", post["message_id"], e)
                    keep.append(post)  # try again next time
                    continue
        if len(keep) != len(posts):
            await self.bot.db.set_state("patch_notes", {**state, "posts": keep})

    @cleanup.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: KoruxaBot) -> None:
    await bot.add_cog(Updates(bot))
