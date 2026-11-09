"""Experimental: clan bank stock-target board + target management.

Officers set targets with /stock-set; /stock compares them to live bank
quantities. Registers ONLY in DEMO_GUILD_ID. Targets live in bot_state
"stock_targets" as {guild_id: [{item_key, name, target}]}.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .. import access
from ..stock import _norm, build_rows_from_targets, demo_rows, resolve_item

log = logging.getLogger(__name__)

BANK_LIMIT = 2000  # share the cached clan-bank response with potions/loans
STATE_KEY = "stock_targets"
BOARD_KEY = "stock_board"  # {guild: {channel_id, message_id?, posts: {target_key: {thread_id, message_id}}}}


def _n(value: int) -> str:
    return f"{int(value):,}"


def _status_colour(emoji: str) -> discord.Colour:
    return {"🔴": discord.Colour.red(), "🟡": discord.Colour.gold()}.get(emoji, discord.Colour.green())


def stock_title(r) -> str:
    emoji, label = r.status
    capped = min(100.0, max(0.0, r.percent))
    return f"{emoji} {r.name} — {_n(r.current)}/{_n(r.target)} ({capped:.0f}%) · {label}"[:100]


def item_movements(log: list[dict], item_key: str, name: str, limit: int = 10):
    """Recent donors (deposits) and takers (withdrawals/gifts) of one item, newest first."""
    key, nm = _norm(item_key), _norm(name)
    donors, takers = [], []
    for e in sorted(log or [], key=lambda x: (str(x.get("at") or ""), x.get("id") or 0), reverse=True):
        ek, en = _norm(e.get("item_key")), _norm(e.get("name"))
        if not ((key and ek == key) or (not key and nm and en == nm)):
            continue
        who = discord.utils.escape_markdown(str(e.get("character") or "?"))
        try:
            qty = int(e.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        action = str(e.get("action", "")).lower()
        if action == "deposit" and len(donors) < limit:
            donors.append(f"{who} · {_n(qty)}")
        elif action in ("withdraw", "give") and len(takers) < limit:
            takers.append(f"{who} · {_n(qty)}")
        if len(donors) >= limit and len(takers) >= limit:
            break
    return donors, takers


def item_embed(r, clan_name: str, donors=None, takers=None) -> discord.Embed:
    """A single item's live status + recent movers, for one forum post."""
    emoji, label = r.status
    capped = min(100.0, max(0.0, r.percent))
    lines = [f"**{_n(r.current)} / {_n(r.target)}** ({capped:.0f}%) — {label}"]
    if r.needed > 0:
        lines.append(f"Need **{_n(r.needed)} more**")
    elif r.over > 0:
        lines.append(f"✅ **{_n(r.over)} over** target")
    else:
        lines.append("✅ Target met")
    embed = discord.Embed(title=f"{emoji} {r.name}", description="\n".join(lines), colour=_status_colour(emoji))
    if donors:
        embed.add_field(name="📥 Recent donors", value="\n".join(donors)[:1024], inline=True)
    if takers:
        embed.add_field(name="📤 Recent takers", value="\n".join(takers)[:1024], inline=True)
    embed.set_footer(text=f"{clan_name} · updates ~every 10 min")
    return embed


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

    async def cog_load(self) -> None:
        self.refresh.start()

    async def cog_unload(self) -> None:
        self.refresh.cancel()

    # ---- persistent board ----
    async def _stock_channel(self, guild_id: int | None):
        cfg = await self.bot.db.get_feature(guild_id, "stock")
        chan = self.bot.get_channel(cfg.channel_id) if cfg and cfg.channel_id else None
        return chan if isinstance(chan, (discord.TextChannel, discord.ForumChannel)) else None

    async def _fetch(self, cid: int):
        chan = self.bot.get_channel(cid)
        if chan is not None:
            return chan
        try:
            return await self.bot.fetch_channel(cid)
        except discord.HTTPException:
            return None

    async def _publish_board(self, guild_id: int, channel) -> None:
        targets = await self._targets(guild_id)
        data = await self._bank_data(guild_id)
        rows = build_rows_from_targets(targets, data.get("items") or [])
        log = data.get("log") or []
        key_of = {_norm(t.get("name")): str(t.get("item_key") or "") for t in targets}
        clan = self.bot.clan_tag(guild_id) or "Your clan"  # type: ignore[attr-defined]
        state = await self.bot.db.get_state(BOARD_KEY) or {}
        gstate = state.setdefault(str(guild_id), {})
        gstate["channel_id"] = channel.id

        if isinstance(channel, discord.ForumChannel):
            posts = gstate.setdefault("posts", {})
            seen = set()
            for r in rows:
                tkey = _norm(r.name)
                seen.add(tkey)
                title = stock_title(r)
                donors, takers = item_movements(log, key_of.get(tkey, ""), r.name)
                embed = item_embed(r, clan, donors, takers)
                info = posts.get(tkey)
                thread = await self._fetch(info["thread_id"]) if info else None
                if isinstance(thread, discord.Thread):
                    if thread.archived:
                        await thread.edit(archived=False)
                    try:
                        msg = await thread.fetch_message(info["message_id"])
                        await msg.edit(embed=embed)
                        if thread.name != title:
                            await thread.edit(name=title)
                        continue
                    except discord.HTTPException:
                        pass
                created = await channel.create_thread(name=title, embed=embed)
                posts[tkey] = {"thread_id": created.thread.id, "message_id": created.message.id}
            for tkey in [k for k in posts if k not in seen]:  # targets that were removed
                info = posts.pop(tkey)
                thread = await self._fetch(info["thread_id"])
                if isinstance(thread, discord.Thread):
                    try:
                        await thread.delete()
                    except discord.HTTPException:
                        pass
        else:
            embed = build_stock_embed(rows, clan)
            mid = gstate.get("message_id")
            if mid:
                try:
                    await (await channel.fetch_message(mid)).edit(embed=embed)
                    await self.bot.db.set_state(BOARD_KEY, state)
                    return
                except discord.HTTPException:
                    pass
            gstate["message_id"] = (await channel.send(embed=embed)).id
        await self.bot.db.set_state(BOARD_KEY, state)

    async def _refresh_board(self, guild_id: int) -> None:
        channel = await self._stock_channel(guild_id)
        if channel is not None:
            try:
                await self._publish_board(guild_id, channel)
            except discord.HTTPException as e:
                log.warning("stock board refresh failed for guild %s: %s", guild_id, e)

    @tasks.loop(minutes=10)
    async def refresh(self) -> None:
        state = await self.bot.db.get_state(BOARD_KEY) or {}
        for gid in list(state):
            await self._refresh_board(int(gid))

    @refresh.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

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
        await self._refresh_board(interaction.guild_id)
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
        await self._refresh_board(interaction.guild_id)
        await interaction.followup.send(f"Removed stock target **{item}**.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Stock(bot))
