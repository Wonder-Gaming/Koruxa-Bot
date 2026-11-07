"""Experimental: weekly clan-potion usage board (image card).

Registers ONLY in the demo/test server (DEMO_GUILD_ID). Uses real clan-bank data
when a clan is linked in the server, else a demo board. When it graduates: drop
the guild scoping in setup() and add a /help section.
"""

from __future__ import annotations

import asyncio
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..potion_card import render_potion_card
from ..potions import demo_usage, summarize_usage, week_start

log = logging.getLogger(__name__)

BANK_LOG_LIMIT = 2000


class Potions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _owner_maps(self):
        """character_id -> owner (discord id) and -> display username, from links."""
        links = await self.bot.db.list_links()
        char_owner = {ln.character_id: ln.discord_id for ln in links}
        char_name = {ln.character_id: ln.username for ln in links if ln.username}
        return char_owner, char_name

    async def _summary(self, guild_id: int | None):
        """Real net usage from the linked clan's bank log, or demo data if none."""
        cog = self.bot.get_cog("Clan")
        tracker = cog.tracker_for(guild_id) if cog else None  # type: ignore[attr-defined]
        if tracker is not None:
            data = await tracker.fetch("clan-bank", {"limit": BANK_LOG_LIMIT})
            if data and data.get("log") is not None:
                char_owner, char_name = await self._owner_maps()
                return summarize_usage(data["log"], week_start(), char_owner, char_name), False
        return demo_usage(), True

    @app_commands.command(name="potions", description="Weekly clan potion usage from the bank (net per player)")
    async def potions(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        summary, is_demo = await self._summary(interaction.guild_id)
        clan = self.bot.clan_tag(interaction.guild_id) or "Your clan"  # type: ignore[attr-defined]
        name = f"{clan}{' (demo)' if is_demo else ''}"
        png = await asyncio.to_thread(render_potion_card, summary, name)
        await interaction.followup.send(file=discord.File(io.BytesIO(png), filename="potions.png"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Potions(bot))
