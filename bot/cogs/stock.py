"""Experimental: clan bank stock-target board + target management.

Officers set targets with /stock-set; /stock compares them to live bank
quantities. Registers ONLY in DEMO_GUILD_ID. Targets live in bot_state
"stock_targets" as {guild_id: [{item_key, name, target}]}.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .. import access
from ..stock import _norm, build_rows_from_targets, demo_rows, resolve_item

log = logging.getLogger(__name__)

BANK_LIMIT = 2000  # share the cached clan-bank response with potions/loans
STATE_KEY = "stock_targets"


def _n(value: int) -> str:
    return f"{int(value):,}"


def _row_field(r) -> dict:
    """Matches the reference bot's stockRowField."""
    capped = min(100.0, max(0.0, r.percent))
    details = [f"**{_n(r.current)} / {_n(r.target)}** ({capped:.1f}%)"]
    if r.needed > 0:
        details.append(f"Need **{_n(r.needed)} more**")
    elif r.over > 0:
        details.append(f"✅ **{_n(r.over)} over**")
    else:
        details.append("✅ Target met")
    return {"name": f"{r.status[0]} {discord.utils.escape_markdown(r.name)}"[:256],
            "value": "\n".join(details)[:1024], "inline": False}


def build_stock_embed(rows, clan_name: str, is_demo: bool = False) -> discord.Embed:
    embed = discord.Embed(title=f"{clan_name} — General Stock")
    if not rows:
        embed.description = "No stock targets set yet. Add one with `/stock-set`."
    for r in rows[:20]:
        embed.add_field(**_row_field(r))
    if is_demo:
        embed.set_footer(text="Demo data — link a clan with /link-clan for live figures")
    return embed


class Stock(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _tracker(self, guild_id: int | None):
        cog = self.bot.get_cog("Clan")
        return cog.tracker_for(guild_id) if cog else None  # type: ignore[attr-defined]

    async def _bank_data(self, guild_id: int | None) -> dict:
        tracker = self._tracker(guild_id)
        if tracker is None:
            return {}
        return await tracker.fetch("clan-bank", {"limit": BANK_LIMIT}) or {}

    async def _bank_items(self, guild_id: int | None) -> list[dict]:
        return (await self._bank_data(guild_id)).get("items") or []

    @staticmethod
    def _catalog(data: dict) -> list[dict]:
        """Distinct (item_key, name) from current items AND the log, so items that
        are temporarily out of stock can still be targeted."""
        seen: dict[str, dict] = {}
        for e in (data.get("items") or []) + (data.get("log") or []):
            key = str(e.get("item_key") or "")
            name = e.get("name")
            if name and key and key not in seen:
                seen[key] = {"item_key": key, "name": str(name)}
        return list(seen.values())

    async def _targets(self, guild_id: int | None) -> list[dict]:
        return (await self.bot.db.get_state(STATE_KEY) or {}).get(str(guild_id), [])

    async def _save_targets(self, guild_id: int | None, targets: list[dict]) -> None:
        state = await self.bot.db.get_state(STATE_KEY) or {}
        state[str(guild_id)] = targets
        await self.bot.db.set_state(STATE_KEY, state)

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        return interaction.guild is not None and interaction.user.guild_permissions.manage_guild

    # ---- autocomplete ----
    async def _bank_item_ac(self, interaction: discord.Interaction, current: str):
        catalog = self._catalog(await self._bank_data(interaction.guild_id))
        cur = _norm(current)
        names = sorted({c["name"] for c in catalog if cur in _norm(c["name"])})
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]

    async def _target_ac(self, interaction: discord.Interaction, current: str):
        targets = await self._targets(interaction.guild_id)
        cur = _norm(current)
        names = [t.get("name") for t in targets if cur in _norm(t.get("name"))]
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]

    # ---- commands ----
    @app_commands.command(name="stock", description="Clan bank stock-target board")
    async def stock(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        clan = self.bot.clan_tag(interaction.guild_id) or "Your clan"  # type: ignore[attr-defined]
        tracker = self._tracker(interaction.guild_id)
        if tracker is None:
            await interaction.followup.send(embed=build_stock_embed(demo_rows(), f"{clan} (demo)", is_demo=True))
            return
        targets = await self._targets(interaction.guild_id)
        items = await self._bank_items(interaction.guild_id)
        rows = build_rows_from_targets(targets, items)
        await interaction.followup.send(embed=build_stock_embed(rows, clan))

    @app_commands.command(name="stock-set", description="Set a clan bank stock target (admins)")
    @app_commands.describe(item="Bank item name", target="Quantity to keep stocked")
    @app_commands.autocomplete(item=_bank_item_ac)
    async def stock_set(self, interaction: discord.Interaction, item: str,
                        target: app_commands.Range[int, 1, 2_000_000_000]) -> None:
        if not await access.can_use(self.bot, interaction, "bank"):
            await access.deny(interaction, "bank")
            return
        await interaction.response.defer(ephemeral=True)
        catalog = self._catalog(await self._bank_data(interaction.guild_id))
        match, candidates = resolve_item(catalog, item)
        if match:
            item_key, name = str(match.get("item_key") or ""), str(match.get("name") or item)
        elif len(candidates) > 1:
            names = ", ".join(c.get("name") for c in candidates[:8])
            await interaction.followup.send(f"Several items match **{item}** — be more specific: {names}", ephemeral=True)
            return
        else:
            item_key, name = "", item.strip()  # not in the bank right now; track by name

        targets = [t for t in await self._targets(interaction.guild_id)
                   if not ((item_key and _norm(t.get("item_key")) == _norm(item_key))
                           or (not item_key and _norm(t.get("name")) == _norm(name)))]
        targets.append({"item_key": item_key, "name": name, "target": int(target)})
        await self._save_targets(interaction.guild_id, targets)
        await interaction.followup.send(f"📦 Target set: **{name}** → keep **{_n(target)}** stocked.", ephemeral=True)

    @app_commands.command(name="stock-remove", description="Remove a clan bank stock target (admins)")
    @app_commands.describe(item="Target to remove")
    @app_commands.autocomplete(item=_target_ac)
    async def stock_remove(self, interaction: discord.Interaction, item: str) -> None:
        if not await access.can_use(self.bot, interaction, "bank"):
            await access.deny(interaction, "bank")
            return
        await interaction.response.defer(ephemeral=True)
        needle = _norm(item)
        targets = await self._targets(interaction.guild_id)
        kept = [t for t in targets if _norm(t.get("name")) != needle and _norm(t.get("item_key")) != needle]
        if len(kept) == len(targets):
            await interaction.followup.send(f"No stock target matching **{item}**.", ephemeral=True)
            return
        await self._save_targets(interaction.guild_id, kept)
        await interaction.followup.send(f"Removed stock target **{item}**.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Stock(bot))
