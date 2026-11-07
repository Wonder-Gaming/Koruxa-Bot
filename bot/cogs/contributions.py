"""Experimental: clan bank contribution board. Registers ONLY in DEMO_GUILD_ID.

Values each player's deposits/withdrawals at current wiki market prices (from the
wiki_items cache) where available, else the bank log's own unit value.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..contrib_card import render_contrib_card
from ..contributions import demo_contributions, summarize_contributions

log = logging.getLogger(__name__)

BANK_LIMIT = 2000


class Contributions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _tracker(self, guild_id: int | None):
        cog = self.bot.get_cog("Clan")
        return cog.tracker_for(guild_id) if cog else None  # type: ignore[attr-defined]

    async def _price_map(self, item_keys: set[str]) -> dict[str, int]:
        """Market unit price per item_key from the wiki cache (if a crawl has run)."""
        if not item_keys:
            return {}
        prices: dict[str, int] = {}
        placeholders = ",".join("?" * len(item_keys))
        try:
            async with self.bot.db.conn.execute(
                f"SELECT item_key, data_json FROM wiki_items WHERE item_key IN ({placeholders})",
                tuple(item_keys)) as cur:
                for key, data_json in await cur.fetchall():
                    price = (json.loads(data_json) or {}).get("market_price")
                    if isinstance(price, int) and price > 0:
                        prices[key] = price
        except Exception as e:  # wiki_items table may not exist yet
            log.debug("contribution price lookup skipped: %s", e)
        return prices

    async def _owner_maps(self):
        links = await self.bot.db.list_links()
        return ({ln.character_id: ln.discord_id for ln in links},
                {ln.character_id: ln.username for ln in links if ln.username})

    @app_commands.command(name="contributions", description="Clan bank contribution board — who funds the clan")
    async def contributions(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        clan = self.bot.clan_tag(interaction.guild_id) or "Your clan"  # type: ignore[attr-defined]
        tracker = self._tracker(interaction.guild_id)
        if tracker is None:
            summary = demo_contributions()
            png = await asyncio.to_thread(render_contrib_card, summary, f"{clan} (demo)")
            await interaction.followup.send(file=discord.File(io.BytesIO(png), filename="contributions.png"))
            return
        data = await tracker.fetch("clan-bank", {"limit": BANK_LIMIT})
        log_rows = (data or {}).get("log") or []
        keys = {str(e.get("item_key", "")) for e in log_rows if e.get("item_key")}
        price_map = await self._price_map(keys)
        char_owner, char_name = await self._owner_maps()
        summary = summarize_contributions(log_rows, price_map, char_owner, char_name)
        png = await asyncio.to_thread(render_contrib_card, summary, clan)
        await interaction.followup.send(file=discord.File(io.BytesIO(png), filename="contributions.png"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Contributions(bot))
