"""Server setup: alert channels, the self-assign role panel, and test alerts (admin only)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..alerts import channel_problems, post_alert
from ..features import FEATURE_CHOICES, FEATURES, ROLE_FEATURES

if TYPE_CHECKING:
    from ..client import KoruxaBot

log = logging.getLogger(__name__)


# ---------- role panel ----------

class RoleToggle(discord.ui.DynamicItem[discord.ui.Button], template=r"koruxa:role:(?P<feature>[a-z]+)"):
    """A role-panel button. Its custom_id carries only the feature, so it keeps working
    after restarts and follows the role if an admin changes it."""

    def __init__(self, feature: str):
        info = FEATURES.get(feature)
        super().__init__(
            discord.ui.Button(
                label=info.label if info else feature,
                emoji=info.emoji if info else None,
                style=discord.ButtonStyle.secondary,
                custom_id=f"koruxa:role:{feature}",
            )
        )
        self.feature = feature

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match) -> RoleToggle:
        return cls(match["feature"])

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: KoruxaBot = interaction.client  # type: ignore[assignment]
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            return
        cfg = await bot.db.get_feature(interaction.guild.id, self.feature)
        role = interaction.guild.get_role(cfg.role_id) if cfg and cfg.role_id else None
        if role is None:
            await interaction.response.send_message(
                "That role isn't set up any more. Ask an admin to run **/rolepanel** again.", ephemeral=True
            )
            return

        label = FEATURES[self.feature].label if self.feature in FEATURES else self.feature
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Koruxa role panel")
                message = f"Removed {role.mention}. You won't be pinged for {label} alerts any more."
            else:
                await member.add_roles(role, reason="Koruxa role panel")
                message = f"Added {role.mention}. You'll be pinged for {label} alerts."
        except discord.Forbidden:
            log.warning("Missing permission to toggle role %s in guild %s", role.id, interaction.guild.id)
            message = (
                f"I'm not allowed to manage {role.mention}. An admin needs to give me **Manage Roles** "
                "and drag my role above it in Server Settings → Roles."
            )
        await interaction.response.send_message(message, ephemeral=True)


def panel_embed() -> discord.Embed:
    lines = []
    for key in ROLE_FEATURES:
        f = FEATURES[key]
        lines.append(f"{f.emoji} **{f.label}**: {f.description}" + ("" if f.live else " *(coming soon)*"))
    return discord.Embed(
        title="🔔 Koruxa alert roles",
        description="Click a button to get pinged for those alerts. Click it again to stop.\n\n" + "\n".join(lines),
        colour=discord.Colour.orange(),
    )


def panel_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for key in ROLE_FEATURES:
        view.add_item(RoleToggle(key))
    return view


# ---------- admin commands ----------

class Setup(commands.Cog):
    def __init__(self, bot: KoruxaBot):
        self.bot = bot

    async def _role(self, guild: discord.Guild, feature: str) -> discord.Role | None:
        cfg = await self.bot.db.get_feature(guild.id, feature)
        return guild.get_role(cfg.role_id) if cfg and cfg.role_id else None

    @app_commands.command(name="setchannel", description="Choose the channel for a type of alert (admin)")
    @app_commands.describe(feature="Which alerts", channel="Where to post them (leave empty to turn the alerts off)")
    @app_commands.choices(feature=FEATURE_CHOICES)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setchannel(
        self,
        interaction: discord.Interaction,
        feature: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        info = FEATURES[feature.value]
        current = await self.bot.db.get_feature(guild.id, info.key)
        await self.bot.db.set_feature_field(guild.id, info.key, "channel_id", channel.id if channel else None)
        if channel is None or current is None or current.channel_id != channel.id:
            # A new channel gets a fresh live status message; the same channel keeps its message.
            await self.bot.db.set_feature_field(guild.id, info.key, "status_message_id", None)
        log.info("Guild %s set %s channel to %s", guild.id, info.key, channel.id if channel else None)

        if channel is None:
            await interaction.response.send_message(f"🔕 {info.emoji} **{info.label}** alerts are off.", ephemeral=True)
            return

        role = await self._role(guild, info.key)
        lines = [f"✅ {info.emoji} **{info.label}** will be posted in {channel.mention}."]
        lines += [f"⚠️ {p}" for p in channel_problems(channel, role)]
        if not info.live:
            lines.append("ℹ️ This feature isn't built yet, so it won't post anything.")
        elif info.clan and not getattr(self.bot.get_cog("Clan"), "token", ""):
            lines.append("ℹ️ This needs the clan's API token, so it won't post anything yet. Set it with **/link-clan**.")
        if info.key in ROLE_FEATURES and role is None:
            lines.append("Tip: run **/rolepanel** so members can opt in to a ping role.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
        if info.key == "help" and (general := self.bot.get_cog("General")):
            await general.publish_help()  # type: ignore[attr-defined]

    @app_commands.command(name="rolepanel", description="Post the buttons members use to pick their alert roles (admin)")
    @app_commands.describe(channel="Where to post the panel (default: this channel)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def rolepanel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        guild = interaction.guild
        assert guild is not None
        target = channel or interaction.channel
        if not guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "I need the **Manage Roles** permission to hand out roles. Add it to my role in "
                "Server Settings → Roles, then run this again.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        notes = []
        for key in ROLE_FEATURES:
            info = FEATURES[key]
            role = await self._role(guild, key)
            if role is None:
                role = discord.utils.get(guild.roles, name=info.label)
                if role is None:
                    role = await guild.create_role(name=info.label, mentionable=False, reason="Koruxa bot: alert ping role")
                    notes.append(f"Created {role.mention}.")
                else:
                    notes.append(f"Using the existing {role.mention} role.")
                await self.bot.db.set_feature_field(guild.id, key, "role_id", role.id)
            if role >= guild.me.top_role:
                notes.append(f"⚠️ {role.mention} is above my role, so I can't assign it. Drag my role above it in Server Settings → Roles.")

        try:
            await target.send(embed=panel_embed(), view=panel_view())
        except (discord.Forbidden, AttributeError):
            await interaction.followup.send(f"I can't post in {getattr(target, 'mention', 'that channel')}. Check my permissions there.")
            return
        log.info("Guild %s posted a role panel in channel %s", guild.id, target.id)
        await interaction.followup.send("\n".join([f"✅ Role panel posted in {target.mention}.", *notes]))

    @app_commands.command(name="alerts", description="Show where each type of alert goes (admin)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def alerts(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        configs = {c.feature: c for c in await self.bot.db.list_features(guild.id)}
        embed = discord.Embed(title="Koruxa alert settings", colour=discord.Colour.orange())
        for info in FEATURES.values():
            cfg = configs.get(info.key)
            channel = guild.get_channel(cfg.channel_id) if cfg and cfg.channel_id else None
            role = guild.get_role(cfg.role_id) if cfg and cfg.role_id else None
            lines = [
                f"Channel: {channel.mention if channel else 'not set'}",
                f"Ping role: {role.mention if role else 'none'}",
            ]
            if not info.live:
                lines.append("*Needs a clan token (not active yet)*")
            if isinstance(channel, discord.TextChannel):
                lines += [f"⚠️ {p}" for p in channel_problems(channel, role)]
            embed.add_field(name=f"{info.emoji} {info.label}", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="testalert", description="Post a test alert, with its role ping, to check the setup (admin)")
    @app_commands.describe(feature="Which alert channel to test")
    @app_commands.choices(feature=FEATURE_CHOICES)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def testalert(self, interaction: discord.Interaction, feature: app_commands.Choice[str]) -> None:
        guild = interaction.guild
        assert guild is not None
        info = FEATURES[feature.value]
        embed = discord.Embed(
            title=f"🧪 {info.emoji} Test {info.label} alert",
            description=f"This is where {info.label} alerts will appear. Members with the ping role were just notified.",
            colour=discord.Colour.orange(),
        )
        sent = await post_alert(self.bot, info.key, embed=embed, guild_id=guild.id)
        if sent:
            await interaction.response.send_message(f"Sent: {sent[guild.id].jump_url}", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Nothing sent. Set a channel first with **/setchannel feature:{info.label}**, and check **/alerts** for permission problems.",
                ephemeral=True,
            )


async def setup(bot: KoruxaBot) -> None:
    bot.add_dynamic_items(RoleToggle)
    await bot.add_cog(Setup(bot))
