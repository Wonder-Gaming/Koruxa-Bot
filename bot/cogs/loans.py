"""Experimental: clan bank loan ledger in an auto-updating thread.

/loans creates (or reuses) a "📒 Loan Ledger" thread in the channel it's run in,
posts a running ledger reconstructed from the clan bank log, and keeps it updated
as the log changes. Registers ONLY in DEMO_GUILD_ID.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..loans import demo_loans, reconstruct_loans, summarize

log = logging.getLogger(__name__)

BANK_LOG_LIMIT = 2000
THREAD_NAME = "📒 Loan Ledger"
STATE_KEY = "loan_ledger"


def _n(value: int) -> str:
    return f"{int(value):,}"


def build_ledger_embed(loans, clan_name: str, is_demo: bool) -> discord.Embed:
    s = summarize(loans)
    active = [ln for ln in loans if ln.active]
    lines = [f"**{clan_name}** · {s['active']} out on loan · {s['returned']} returned/settled",
             f"{_n(s['outstanding_items'])} sealed item(s) outstanding"]
    embed = discord.Embed(title="Clan Bank Loans", description="\n".join(lines))
    if not active:
        embed.description += "\n\nNo sealed items are currently out on loan."
    for ln in active[:20]:
        borrower = discord.utils.escape_markdown(ln.borrower)
        value = (f"📦 **{borrower}** · **{_n(ln.outstanding)}** outstanding"
                 f"{f' of {_n(ln.borrowed)} borrowed' if ln.borrowed != ln.outstanding else ''}\n"
                 f"Borrowed <t:{int(ln.last_at.timestamp())}:R>")
        embed.add_field(name=discord.utils.escape_markdown(ln.item_name)[:256], value=value[:1024], inline=False)
    footer = "Nets out alt-to-alt moves · updates as the bank log changes"
    embed.set_footer(text=footer + (" · demo data" if is_demo else ""))
    embed.timestamp = datetime.now(timezone.utc)
    return embed


class Loans(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.refresh.start()

    async def cog_unload(self) -> None:
        self.refresh.cancel()

    def _tracker(self, guild_id: int | None):
        cog = self.bot.get_cog("Clan")
        return cog.tracker_for(guild_id) if cog else None  # type: ignore[attr-defined]

    async def _owner_maps(self):
        links = await self.bot.db.list_links()
        return ({ln.character_id: ln.discord_id for ln in links},
                {ln.character_id: ln.username for ln in links if ln.username})

    async def _loans_for(self, guild_id: int | None):
        tracker = self._tracker(guild_id)
        if tracker is not None:
            data = await tracker.fetch("clan-bank", {"limit": BANK_LOG_LIMIT})
            if data and data.get("log") is not None:
                log_rows = data["log"]
                newest = max((e.get("id") or 0) for e in log_rows) if log_rows else 0
                char_owner, char_name = await self._owner_maps()
                return reconstruct_loans(log_rows, char_owner, char_name), newest, False
        return demo_loans(), 0, True

    @app_commands.command(name="loans", description="Post/refresh the clan bank loan ledger thread")
    async def loans(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        loans, newest, is_demo = await self._loans_for(interaction.guild_id)
        clan = self.bot.clan_tag(interaction.guild_id) or "Your clan"  # type: ignore[attr-defined]
        embed = build_ledger_embed(loans, f"{clan}{' (demo)' if is_demo else ''}", is_demo)

        state = await self.bot.db.get_state(STATE_KEY) or {}
        entry = state.get(str(interaction.guild_id))
        try:
            thread, message = await self._place(interaction, entry, embed)
        except discord.Forbidden:
            await interaction.followup.send(
                "I need **Create Public Threads** + **Send Messages in Threads** in this channel to post the ledger.",
                ephemeral=True)
            return

        state[str(interaction.guild_id)] = {
            "channel_id": thread.parent_id, "thread_id": thread.id,
            "message_id": message.id, "last_log_id": newest,
        }
        await self.bot.db.set_state(STATE_KEY, state)
        await interaction.followup.send(f"Loan ledger posted in {thread.mention}.", ephemeral=True)

    async def _place(self, interaction, entry, embed):
        """Reuse the stored thread/message if possible, else make a fresh thread."""
        if entry:
            try:
                thread = self.bot.get_channel(entry["thread_id"]) or await self.bot.fetch_channel(entry["thread_id"])
                if isinstance(thread, discord.Thread) and thread.archived:
                    await thread.edit(archived=False)
                message = await thread.fetch_message(entry["message_id"])
                await message.edit(embed=embed)
                return thread, message
            except (discord.NotFound, discord.HTTPException, KeyError):
                pass
        base = interaction.channel
        if isinstance(base, discord.Thread):
            thread = base
        else:
            thread = await base.create_thread(name=THREAD_NAME, type=discord.ChannelType.public_thread)
        message = await thread.send(embed=embed)
        return thread, message

    @tasks.loop(minutes=5)
    async def refresh(self) -> None:
        state = await self.bot.db.get_state(STATE_KEY) or {}
        changed = False
        for gid, entry in list(state.items()):
            loans, newest, is_demo = await self._loans_for(int(gid))
            if is_demo or newest == entry.get("last_log_id"):
                continue
            clan = self.bot.clan_tag(int(gid)) or "Your clan"  # type: ignore[attr-defined]
            embed = build_ledger_embed(loans, clan, is_demo=False)
            try:
                thread = self.bot.get_channel(entry["thread_id"]) or await self.bot.fetch_channel(entry["thread_id"])
                if isinstance(thread, discord.Thread) and thread.archived:
                    await thread.edit(archived=False)
                message = await thread.fetch_message(entry["message_id"])
                await message.edit(embed=embed)
                entry["last_log_id"] = newest
                changed = True
            except discord.HTTPException as e:
                log.warning("Couldn't refresh loan ledger in guild %s: %s", gid, e)
        if changed:
            await self.bot.db.set_state(STATE_KEY, state)

    @refresh.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Loans(bot))
