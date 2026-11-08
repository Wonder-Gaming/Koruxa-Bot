"""Gear & augment guides, each published as a thread in the Guides channel.

/guides builds every guide from the cached wiki data and posts it into its own
thread (one message per bracket/section), editing in place so threads stay fresh
rather than being re-spammed. Refreshes daily. Set the channel with
/setchannel guides #channel.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import access
from ..guides import build_guides, load_gear

log = logging.getLogger(__name__)

STATE_KEY = "guides_pub"
COLOUR = 0x6E93B8


class Guides(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.daily.start()

    async def cog_unload(self) -> None:
        self.daily.cancel()

    async def _build(self) -> list[dict]:
        async with self.bot.db.conn.execute("SELECT data_json FROM wiki_items") as cur:
            rows = await cur.fetchall()
        items = load_gear(rows)
        augs = (await self.bot.db.get_state("wiki_augments") or {}).get("augments", [])
        return build_guides(items, augs)

    async def _fetch_channel(self, cid: int):
        chan = self.bot.get_channel(cid)
        if chan is not None:
            return chan
        try:
            return await self.bot.fetch_channel(cid)
        except discord.HTTPException:
            return None

    async def _guides_channel(self, guild_id: int | None):
        cfg = await self.bot.db.get_feature(guild_id, "guides")
        chan = self.bot.get_channel(cfg.channel_id) if cfg and cfg.channel_id else None
        return chan if isinstance(chan, discord.TextChannel) else None

    async def _publish(self, guild_id: int, channel: discord.TextChannel) -> int:
        guides = await self._build()
        state = await self.bot.db.get_state(STATE_KEY) or {}
        gstate = state.setdefault(str(guild_id), {})
        gstate["channel_id"] = channel.id
        threads = gstate.setdefault("threads", {})

        for guide in guides:
            info = threads.get(guide["key"]) or {}
            thread = None
            if info.get("thread_id"):
                thread = await self._fetch_channel(info["thread_id"])
                if isinstance(thread, discord.Thread) and thread.archived:
                    await thread.edit(archived=False)
            if not isinstance(thread, discord.Thread):
                thread = await channel.create_thread(
                    name=f"{guide['emoji']} {guide['title']}"[:100], type=discord.ChannelType.public_thread)
                info = {"thread_id": thread.id, "message_ids": []}
            old_ids = info.get("message_ids", [])
            new_ids = []
            for i, msg in enumerate(guide["messages"]):
                embed = discord.Embed(title=msg["title"], description=msg["description"], colour=COLOUR)
                if i < len(old_ids):
                    try:
                        m = await thread.fetch_message(old_ids[i])
                        await m.edit(embed=embed)
                        new_ids.append(m.id)
                        continue
                    except discord.HTTPException:
                        pass
                sent = await thread.send(embed=embed)
                new_ids.append(sent.id)
            for stale in old_ids[len(guide["messages"]):]:
                try:
                    await (await thread.fetch_message(stale)).delete()
                except discord.HTTPException:
                    pass
            info["message_ids"] = new_ids
            threads[guide["key"]] = info

        await self.bot.db.set_state(STATE_KEY, state)
        return len(guides)

    @app_commands.command(name="guides", description="Publish/refresh the gear & augment guide threads (admin)")
    async def guides(self, interaction: discord.Interaction) -> None:
        if not await access.can_use(self.bot, interaction, "guides"):
            await access.deny(interaction, "guides")
            return
        channel = await self._guides_channel(interaction.guild_id)
        if channel is None:
            await interaction.response.send_message(
                "Set a guides channel first: `/setchannel guides #channel`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            n = await self._publish(interaction.guild_id, channel)
        except discord.Forbidden:
            await interaction.followup.send(
                "I need **Create Public Threads** + **Send Messages in Threads** in that channel.", ephemeral=True)
            return
        await interaction.followup.send(f"Published/refreshed **{n}** guide threads in {channel.mention}.", ephemeral=True)

    @tasks.loop(hours=24)
    async def daily(self) -> None:
        state = await self.bot.db.get_state(STATE_KEY) or {}
        for gid in list(state):
            channel = await self._guides_channel(int(gid))
            if channel is not None:
                try:
                    await self._publish(int(gid), channel)
                except discord.HTTPException as e:
                    log.warning("guides refresh failed for guild %s: %s", gid, e)

    @daily.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Guides(bot))
