"""Experimental: Koruxa wiki item lookup, backed by a cached crawl.

/wiki looks items up from the local wiki_items cache (built by an honest,
robots-compliant crawler — see bot/wiki_cache.py). /wiki-sync (admin) runs a full
crawl. Registers ONLY in DEMO_GUILD_ID.
"""

from __future__ import annotations

import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import access
from ..wiki_cache import WikiCache

log = logging.getLogger(__name__)



def _n(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def build_wiki_embed(item: dict) -> discord.Embed:
    embed = discord.Embed(title=item.get("name") or "Wiki item")
    if item.get("url"):
        embed.url = item["url"]
    lines = []
    cr = " · ".join(x for x in (item.get("category"), item.get("rarity")) if x)
    if cr:
        lines.append(cr)
    if item.get("required_level") is not None:
        lines.append(f"Level **{_n(item['required_level'])}**")
    if item.get("equip_slot"):
        lines.append(item["equip_slot"] + (f" · {item['weapon_type']}" if item.get("weapon_type") else ""))
    if item.get("market_price") is not None:
        approx = "~" if item.get("market_approx") else ""
        vol = f" · {_n(item['market_volume_7d'])} traded/7d" if item.get("market_volume_7d") is not None else ""
        lines.append(f"Market **{approx}{_n(item['market_price'])}** coins{vol}")
    if item.get("npc_sell") is not None:
        lines.append(f"NPC sell **{_n(item['npc_sell'])}** coins")
    if item.get("stats_text"):
        lines.append(item["stats_text"])
    if item.get("heal") is not None:
        lines.append(f"Heals **{_n(item['heal'])}**")
    embed.description = "\n".join(lines) or "Cached wiki item."

    if item.get("recipes"):
        val = "\n".join(f"• {r['skill']} **{r['level']}** · {r['recipe']}" for r in item["recipes"][:8])
        embed.add_field(name="Production / gathering", value=val[:1024], inline=False)
    drops = item.get("drops") or []
    if drops:
        lines2 = [f"• {d['source']} · {d['qty']} · {d['chance']}" for d in drops[:10]]
        if len(drops) > 10:
            lines2.append(f"• +{len(drops) - 10} more")
        embed.add_field(name="Monster drops", value="\n".join(lines2)[:1024], inline=False)
    if item.get("thieving"):
        val = "\n".join(f"• {t['source']} · {t['qty']} · {t['chance']}" for t in item["thieving"][:8])
        embed.add_field(name="Stolen from (Thieving)", value=val[:1024], inline=False)
    embed.set_footer(text="Cached from the Koruxa wiki")
    return embed


class Wiki(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cache = WikiCache(bot.db)

    async def cog_load(self) -> None:
        await self.cache.setup()
        if await self.cache.count() == 0:
            log.info("wiki cache empty — starting initial full crawl in the background")
            self.bot.loop.create_task(self.cache.sync())
        self.daily_sync.start()

    async def cog_unload(self) -> None:
        self.daily_sync.cancel()
        await self.cache.close()

    @tasks.loop(hours=24)
    async def daily_sync(self) -> None:
        await self.cache.sync()

    @daily_sync.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _item_ac(self, interaction: discord.Interaction, current: str):
        results = await self.cache.search(current or "a", 25)
        return [app_commands.Choice(name=r["name"][:100], value=r["name"][:100]) for r in results]

    @app_commands.command(name="wiki", description="Look up a Koruxa item (stats, price, recipes, drops)")
    @app_commands.describe(item="Item name")
    @app_commands.autocomplete(item=_item_ac)
    async def wiki(self, interaction: discord.Interaction, item: str) -> None:
        await interaction.response.defer()
        results = await self.cache.search(item, 10)
        found = None
        for r in results:  # prefer an exact name match
            if r["name"].lower() == item.lower():
                found = await self.cache.get(r["item_key"])
                break
        if found is None and len(results) == 1:
            found = await self.cache.get(results[0]["item_key"])
        if found is None:  # on-demand fetch by slug
            found = await self.cache.get(_slug(item)) or await self.cache.refresh_one(_slug(item))
        if found is None:
            names = ", ".join(r["name"] for r in results[:8])
            hint = f" Did you mean: {names}?" if names else ""
            await interaction.followup.send(f"No wiki item found for **{item}**.{hint}", ephemeral=True)
            return
        await interaction.followup.send(embed=build_wiki_embed(found))

    @app_commands.command(name="wiki-sync", description="Crawl/refresh the wiki cache (admins)")
    async def wiki_sync(self, interaction: discord.Interaction) -> None:
        if not await access.can_use(self.bot, interaction, "wiki"):
            await access.deny(interaction, "wiki")
            return
        cached = await self.cache.count()
        if self.cache.syncing:
            s = self.cache.status
            await interaction.response.send_message(
                f"⏳ Crawl in progress: **{s['done']}/{s['total']}** items. ({_n(cached)} cached so far.)",
                ephemeral=True)
            return
        await interaction.response.send_message(
            f"🔎 Starting wiki crawl (~3,900 items, ~45 min). {_n(cached)} currently cached. "
            "Re-run `/wiki-sync` to check progress.", ephemeral=True)
        self.bot.loop.create_task(self.cache.sync())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Wiki(bot))
