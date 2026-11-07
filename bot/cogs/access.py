"""Experimental: per-command access control config. Registers ONLY in DEMO_GUILD_ID.

Admins configure, per scope, who can run the gated admin commands — by Discord
role and/or IN-GAME clan rank. See bot/access.py for the engine.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .. import access

log = logging.getLogger(__name__)


_SCOPE_CHOICES = [app_commands.Choice(name=f"{k} — {v}"[:100], value=k) for k, v in access.SCOPES.items()]
_MODE_CHOICES = [
    app_commands.Choice(name="managers — Manage Server only (default)", value="managers"),
    app_commands.Choice(name="everyone — anyone", value="everyone"),
    app_commands.Choice(name="custom — roles and/or in-game ranks", value="custom"),
]


class Access(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _admin(interaction: discord.Interaction) -> bool:
        return interaction.guild is not None and interaction.user.guild_permissions.manage_guild

    async def _rank_ac(self, interaction: discord.Interaction, current: str):
        names = await access.rank_names(self.bot, interaction.guild_id)
        cur = current.casefold()
        out = [(slug, name) for slug, name in names.items() if cur in name.casefold() or cur in slug]
        return [app_commands.Choice(name=name[:100], value=slug) for slug, name in out[:25]]

    @app_commands.command(name="access-show", description="Show who can use the gated admin commands")
    async def access_show(self, interaction: discord.Interaction) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message("Admins only (Manage Server).", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        names = await access.rank_names(self.bot, interaction.guild_id)
        embed = discord.Embed(title="Command access", colour=discord.Colour.blurple())
        for scope, label in access.SCOPES.items():
            cfg = await access.get_config(self.bot, interaction.guild_id, scope)
            mode = cfg.get("mode", access.DEFAULT_MODE)
            lines = [f"Mode: **{mode}**"]
            if mode == "custom":
                roles = ", ".join(f"<@&{rid}>" for rid in cfg.get("roles", [])) or "—"
                ranks = ", ".join(names.get(s, s) for s in cfg.get("ranks", [])) or "—"
                lines.append(f"Roles: {roles}")
                lines.append(f"In-game ranks: {ranks}")
            embed.add_field(name=label, value="\n".join(lines), inline=False)
        embed.set_footer(text="Manage Server always has access. /access-set to change.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="access-set", description="Set who can use a gated command group (admins)")
    @app_commands.describe(scope="Which commands", mode="Access mode",
                           role="(custom) a Discord role to allow", rank="(custom) an in-game clan rank to allow")
    @app_commands.choices(scope=_SCOPE_CHOICES, mode=_MODE_CHOICES)
    @app_commands.autocomplete(rank=_rank_ac)
    async def access_set(self, interaction: discord.Interaction, scope: app_commands.Choice[str],
                         mode: app_commands.Choice[str] | None = None,
                         role: discord.Role | None = None, rank: str | None = None) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message("Admins only (Manage Server).", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        cfg = dict(await access.get_config(self.bot, interaction.guild_id, scope.value))
        cfg.setdefault("roles", [])
        cfg.setdefault("ranks", [])
        if mode is not None:
            cfg["mode"] = mode.value
        if role is not None:
            if role.id not in cfg["roles"]:
                cfg["roles"].append(role.id)
            cfg["mode"] = "custom"
        if rank:
            if rank not in cfg["ranks"]:
                cfg["ranks"].append(rank)
            cfg["mode"] = "custom"
        cfg.setdefault("mode", access.DEFAULT_MODE)
        await access.set_config(self.bot, interaction.guild_id, scope.value, cfg)
        names = await access.rank_names(self.bot, interaction.guild_id)
        extra = ""
        if cfg["mode"] == "custom":
            roles = ", ".join(f"<@&{r}>" for r in cfg["roles"]) or "—"
            ranks = ", ".join(names.get(s, s) for s in cfg["ranks"]) or "—"
            extra = f"\nRoles: {roles}\nRanks: {ranks}"
        await interaction.followup.send(f"**{scope.value}** access → **{cfg['mode']}**.{extra}", ephemeral=True)

    @app_commands.command(name="access-reset", description="Reset a command group to admins-only (admins)")
    @app_commands.choices(scope=_SCOPE_CHOICES)
    async def access_reset(self, interaction: discord.Interaction, scope: app_commands.Choice[str]) -> None:
        if not self._admin(interaction):
            await interaction.response.send_message("Admins only (Manage Server).", ephemeral=True)
            return
        await access.set_config(self.bot, interaction.guild_id, scope.value, None)
        await interaction.response.send_message(f"**{scope.value}** reset to managers-only.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Access(bot))
