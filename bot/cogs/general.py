import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..alerts import update_status

log = logging.getLogger(__name__)

# /help sections, by the cog a command lives in, in display order.
SECTIONS = [
    ("General", "ℹ️ General"),
    ("Account", "👤 Your account"),
    ("Rates", "🧮 Tools"),
    ("Wiki", "📜 Wiki"),
    ("Feed", "📈 Progress"),
    ("Clan", "🏰 Clan"),
    ("Contributions", "🏦 Bank contributions"),
    ("Stock", "📦 Bank stock"),
    ("Loans", "📒 Bank loans"),
    ("Potions", "🧪 Potion usage"),
    ("SkillFocus", "🎯 Skill focus"),
    ("WorldBoss", "🌀 World Boss"),
    ("Access", "🔐 Access control (admins)"),
    ("Setup", "🛠️ Server setup (admins)"),
]


def _usage(cmd: app_commands.Command) -> str:
    parts = [f"<{p.name}>" if p.required else f"[{p.name}]" for p in cmd.parameters]
    return " ".join([f"/{cmd.name}", *parts])


def _param_line(p: app_commands.Parameter) -> str:
    line = f"> `{p.name}`{'' if p.required else ' (optional)'}: {p.description}"
    if p.choices:
        # Long lists (e.g. 15 skills) would overflow the section; Discord shows them when typing anyway.
        line += f" ({', '.join(c.name for c in p.choices)})" if len(p.choices) <= 5 else " (pick from the list)"
    return line


def help_embeds(tree: app_commands.CommandTree, mentions: dict[str, str], is_admin: bool) -> list[discord.Embed]:
    """The command list, split across as many embeds as Discord's limits need
    (max 25 fields / 6,000 chars per embed) so it keeps working as commands grow."""
    by_cog: dict[str, list[app_commands.Command]] = {}
    for cmd in tree.get_commands():
        if not isinstance(cmd, app_commands.Command):
            continue
        if cmd.default_permissions is not None and not is_admin:
            continue
        cog = type(cmd.binding).__name__ if cmd.binding else "General"
        by_cog.setdefault(cog, []).append(cmd)

    order = [cog for cog, _ in SECTIONS] + sorted(set(by_cog) - {cog for cog, _ in SECTIONS})
    titles = dict(SECTIONS)

    def new_embed(first: bool) -> discord.Embed:
        return discord.Embed(
            title="Koruxa Bot commands" if first else "Koruxa Bot commands (continued)",
            description="Click a command to use it. `<option>` is required, `[option]` is optional." if first else None,
            colour=discord.Colour.orange())

    def length(e: discord.Embed) -> int:
        return len(e.title or "") + len(e.description or "") + sum(len(f.name or "") + len(f.value or "") for f in e.fields)

    embeds = [new_embed(True)]
    for cog in order:
        chunks: list[str] = []
        for cmd in sorted(by_cog.get(cog, []), key=lambda c: c.name):
            # Command + description + usage line. Per-option detail is left out so the
            # whole list fits Discord's 6,000-char message limit; the slash UI shows
            # each option's description as you type anyway.
            lines = [f"{mentions.get(cmd.name, f'`/{cmd.name}`')} {cmd.description}"]
            if cmd.parameters:
                lines.append(f"> `{_usage(cmd)}`")
            block = "\n".join(lines)[:1024]
            if chunks and len(chunks[-1]) + 1 + len(block) <= 1024:
                chunks[-1] += "\n" + block
            else:
                chunks.append(block)
        for i, chunk in enumerate(chunks):
            name = titles.get(cog, cog) if i == 0 else "\u200b"
            if len(embeds[-1].fields) >= 24 or length(embeds[-1]) + len(name) + len(chunk) > 5500:
                embeds.append(new_embed(False))
            embeds[-1].add_field(name=name, value=chunk, inline=False)
    return embeds


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._mentions: dict[str, str] | None = None

    @app_commands.command(name="ping", description="Check that the bot is alive")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! Gateway latency: {latency_ms} ms", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.publish_help()

    async def publish_help(self) -> None:
        """Post or refresh the standing command list in each server's Help Menu channel."""
        if not await self.bot.db.feature_targets("help"):
            return
        embeds = help_embeds(self.bot.tree, await self._command_mentions(), is_admin=False)
        embeds[0].title = "📖 Koruxa Bot commands"
        embeds[-1].set_footer(text="Replies are only visible to you unless you choose public. Admins: /help also lists setup commands.")
        await update_status(self.bot, "help", embeds=embeds[:10])

    @app_commands.command(name="help", description="List the bot's commands and their options")
    async def help(self, interaction: discord.Interaction) -> None:
        mentions = await self._command_mentions()
        # Admin-only commands are hidden from members who can't use them anyway.
        is_admin = interaction.guild is not None and interaction.permissions.manage_guild

        embeds = help_embeds(self.bot.tree, mentions, is_admin)
        await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)

    async def _command_mentions(self) -> dict[str, str]:
        """Clickable </name:id> mentions; needs the IDs Discord assigned at sync."""
        if self._mentions is None:
            guild_id = getattr(self.bot, "config", None) and self.bot.config.guild_id
            try:
                fetched = await self.bot.tree.fetch_commands(guild=discord.Object(guild_id) if guild_id else None)
                self._mentions = {c.name: c.mention for c in fetched}
            except discord.HTTPException as e:
                log.warning("Couldn't fetch command IDs for /help: %s", e)
                return {}
        return self._mentions


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
