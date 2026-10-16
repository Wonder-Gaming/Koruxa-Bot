"""The Discord client: owns shared services, loads cogs and syncs slash commands."""

import hashlib
import json
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from .api import KoruxaAPI
from .config import Config
from .crypto import TokenCipher
from .db import Database

log = logging.getLogger(__name__)

# The bot never joins voice, so skip the "voice will NOT be supported" warnings.
discord.VoiceClient.warn_nacl = False
discord.VoiceClient.warn_dave = False

EXTENSIONS = [
    "bot.cogs.general",
    "bot.cogs.account",
    "bot.cogs.setup",
    "bot.cogs.worldboss",
    "bot.cogs.highscores",
    "bot.cogs.feed",
    "bot.cogs.rates",
    "bot.cogs.clan",
]


class KoruxaBot(commands.Bot):
    def __init__(self, config: Config):
        # Slash commands only: no message content or member intents needed.
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.db = Database(os.path.join(config.data_dir, "bot.db"))
        self.api = KoruxaAPI()
        self.cipher = TokenCipher(config.encryption_key)
        self._membership: dict[tuple[int, int], tuple[bool, float]] = {}

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.api.start()
        self.tree.error(self.on_app_command_error)

        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded extension %s", ext)

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        """Register slash commands with Discord, but only when they changed:
        Discord rate-limits syncing, which slows down back-to-back restarts."""
        guild = discord.Object(id=self.config.guild_id) if self.config.guild_id else None
        if guild:
            self.tree.copy_global_to(guild=guild)
        payload = [cmd.to_dict(self.tree) for cmd in self.tree.get_commands(guild=guild)]
        digest = hashlib.sha256(json.dumps([payload, self.config.guild_id], sort_keys=True).encode()).hexdigest()

        if (await self.db.get_state("command_sync") or {}).get("digest") == digest:
            log.info("Slash commands unchanged; skipping sync")
            return
        synced = await self.tree.sync(guild=guild)
        await self.db.set_state("command_sync", {"digest": digest})
        log.info("Synced %d command(s) %s", len(synced), f"to guild {self.config.guild_id}" if guild else "globally")

    async def close(self) -> None:
        await super().close()
        await self.api.close()
        await self.db.close()

    async def is_member(self, guild: discord.Guild, user_id: int, ttl: float = 3600) -> bool:
        """Whether a user is in `guild`. Cached, since without the members intent it's a REST call."""
        cached = self._membership.get((guild.id, user_id))
        if cached and time.monotonic() - cached[1] < ttl:
            return cached[0]
        if guild.get_member(user_id) is not None:
            result = True
        else:
            try:
                await guild.fetch_member(user_id)
                result = True
            except discord.NotFound:
                result = False
            except discord.HTTPException:
                return cached[0] if cached else False
        self._membership[(guild.id, user_id)] = (result, time.monotonic())
        return result

    @property
    def clan_tag(self) -> str:
        """The clan the highscores, feed and /topgains count: the loaded clan token's clan,
        else CLAN_TAG from .env ("" = everyone)."""
        return getattr(self.get_cog("Clan"), "clan_tag", "") or self.config.clan_tag

    async def clan_snapshots(self, guild: discord.Guild) -> list:
        """Linked characters shown on this server's highscores and feed: in the clan (see clan_tag),
        and linked by someone who is a member of the server."""
        tag = self.clan_tag.lower()
        result = []
        for snap in await self.db.list_snapshots():
            if tag and (snap.clan_tag or "").lower() != tag:
                continue
            if await self.is_member(guild, snap.discord_id):
                result.append(snap)
        return result

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s) in %d server(s)", self.user, self.user.id, len(self.guilds))

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # Only the command name: modal submissions carry the user's token in their data.
        if interaction.type is discord.InteractionType.application_command:
            log.info("User %s ran /%s", interaction.user.id, (interaction.data or {}).get("name"))

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        name = interaction.command.qualified_name if interaction.command else "?"
        log.error("Command /%s failed for user %s", name, interaction.user.id, exc_info=error)
        message = "Something went wrong running that command. The error has been logged."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
