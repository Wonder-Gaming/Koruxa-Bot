"""/link, /unlink, /characters, /profile and /budget: a user's own Koruxa characters (up to 4)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from ..api import KoruxaError, RateLimited, TokenRevoked
from ..crypto import TokenDecryptError
from ..db import MAX_CHARACTERS, Link
from ..fmt import DEFAULT_COLOUR, bar, colour, compact, num, timestamp, title
from ..skills import SKILL_GROUPS, skill_label

if TYPE_CHECKING:
    from ..client import KoruxaBot

log = logging.getLogger(__name__)

NOT_LINKED = "You haven't linked a Koruxa account yet. Use **/link** with a Player API token from the game's **Settings > Account > API access**."
def _clan_line(me: dict[str, Any]) -> str | None:
    clan = me.get("clan")
    if not isinstance(clan, dict):
        return None
    line = f"[{clan.get('tag', '?')}] {clan.get('name', '?')}"
    return f"{line} · {clan['role']}" if clan.get("role") else line


def profile_embed(me: dict[str, Any]) -> discord.Embed:
    player_title = me.get("title") if isinstance(me.get("title"), dict) else {}
    description = [
        _clan_line(me),
        " · ".join(p for p in (player_title.get("label"), me.get("rank_badge"), title(me.get("game_mode"))) if p),
    ]
    embed = discord.Embed(
        title=me.get("username", "Unknown player"),
        description="\n".join(d for d in description if d),
        colour=colour(me.get("rank_color")),
        timestamp=timestamp(me.get("generated_at")),
    )

    status = "🟢 Online" if me.get("is_online") else "⚫ Offline"
    if me.get("is_premium"):
        status += " · ⭐ Premium"
    embed.add_field(name="Status", value=status)
    embed.add_field(name="Combat level", value=num(me.get("combat_level")))
    embed.add_field(name="Total level", value=num(me.get("total_level")))
    embed.add_field(name="Total XP", value=num(me.get("total_xp")))
    embed.add_field(name="Coins", value=num(me.get("coins")))
    embed.add_field(name="Quest points", value=f"{num(me.get('quest_points'))} / {num(me.get('max_quest_points'))}")

    boss = me.get("boss")
    if isinstance(boss, dict):
        embed.add_field(
            name="Clan boss",
            value=(
                f"{num(boss.get('total_kills'))} kills / {num(boss.get('total_fights'))} fights\n"
                f"Best hit {compact(boss.get('best_single_hit'))} · "
                f"lifetime {compact(boss.get('total_lifetime_damage'))}"
            ),
            inline=False,
        )

    # Skills grouped and ordered like the in-game sidebar; unknown new skills go last.
    levels = {
        str(s.get("skill_key")).lower(): s.get("level", "?")
        for s in me.get("skills") or []
        if isinstance(s, dict)
    }
    grouped = {key for _, _, keys in SKILL_GROUPS for key in keys}
    groups = [(f"{emoji} {label}", keys) for label, emoji, keys in SKILL_GROUPS]
    groups.append(("▫️ Other", sorted(k for k in levels if k not in grouped)))
    for name, keys in groups:
        lines = [f"`{levels[k]:>3}` {skill_label(k)}" for k in keys if k in levels]
        if lines:
            embed.add_field(name=name, value="\n".join(lines))

    embed.set_footer(text=f"Koruxa · {num(me.get('days_played'))} days played")
    return embed


def budget_embed(me: dict[str, Any], clan_bank: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title="Clan bank withdrawal budget",
        description=_clan_line(me),
        colour=DEFAULT_COLOUR,
        timestamp=timestamp(me.get("generated_at")),
    )
    cap = clan_bank.get("daily_cap")
    remaining = clan_bank.get("remaining_today")
    if clan_bank.get("cap_exempt"):
        embed.add_field(name="Remaining today", value="No daily cap (exempt)", inline=False)
    else:
        fraction = remaining / cap if isinstance(remaining, (int, float)) and isinstance(cap, (int, float)) and cap else 0
        embed.add_field(
            name="Remaining today",
            value=f"**{num(remaining)}** / {num(cap)}\n{bar(fraction)}",
            inline=False,
        )
    embed.add_field(name="Withdrawn today", value=num(clan_bank.get("withdrawn_today")))
    embed.add_field(name="Can withdraw", value="Yes" if clan_bank.get("can_withdraw") else "No")
    embed.add_field(name="Bank rank", value=title(clan_bank.get("rank")))
    embed.add_field(name="Member for", value=f"{num(clan_bank.get('member_days'))} days")

    # The in-game tooltip says the daily limit resets at 00:00 UTC.
    reset = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    embed.add_field(name="Resets", value=f"{discord.utils.format_dt(reset, 'R')} ({discord.utils.format_dt(reset, 't')})")
    embed.set_footer(text="Koruxa")
    return embed


# ---------- /link modal ----------

class LinkModal(discord.ui.Modal, title="Link a Koruxa character"):
    token = discord.ui.TextInput(
        label="Player API token",
        placeholder="kxu_…  (Settings > Account > API access > New token)",
        min_length=10,
        max_length=200,
    )

    def __init__(self, cog: Account):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.finish_link(interaction, self.token.value.strip())

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("Link modal failed for user %s", interaction.user.id, exc_info=error)
        message = "Something went wrong while linking. The error has been logged."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


# ---------- character option ----------

async def character_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """The user's own linked characters (the default one first)."""
    bot: KoruxaBot = interaction.client  # type: ignore[assignment]
    links = await bot.db.user_links(interaction.user.id)
    choices = [
        app_commands.Choice(name=(link.username or f"Character {link.character_id}") + (" (default)" if i == 0 else ""),
                            value=str(link.character_id))
        for i, link in enumerate(links)
    ]
    return [c for c in choices if current.lower() in c.name.lower()][:25]


CHARACTER_HELP = "Which of your linked characters (default: the first one you linked)"


@dataclass
class Linked:
    link: Link
    token: str

    @property
    def name(self) -> str:
        return self.link.username or f"character {self.link.character_id}"


# ---------- the cog ----------

class Account(commands.Cog):
    def __init__(self, bot: KoruxaBot):
        self.bot = bot

    @app_commands.command(name="link", description=f"Link a Koruxa character with its Player API token (up to {MAX_CHARACTERS})")
    async def link(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(LinkModal(self))

    async def finish_link(self, interaction: discord.Interaction, token: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if any(c.isspace() for c in token):
            await interaction.followup.send("That doesn't look like a token: it contains spaces. Copy it again from the game.")
            return

        try:
            me = await self.bot.api.get(token, "me", fresh=True, max_wait=10)
        except TokenRevoked:
            await interaction.followup.send(
                "Koruxa rejected that token. Check you copied all of it and that it hasn't been revoked."
            )
            return
        except RateLimited as e:
            await interaction.followup.send(f"That token has hit its API rate limit. Try again in about {e.retry_after:.0f} s.")
            return
        except KoruxaError as e:
            log.warning("Link check failed for user %s: %s", interaction.user.id, e)
            await interaction.followup.send("Couldn't reach the Koruxa API to check the token. Try again shortly.")
            return

        character_id = me.get("id")
        if character_id is None:
            await interaction.followup.send("Koruxa didn't return a character for that token. Try again shortly.")
            return
        links = await self.bot.db.user_links(interaction.user.id)
        existing = next((l for l in links if l.character_id == character_id), None)
        if existing is None and len(links) >= MAX_CHARACTERS:
            names = ", ".join(f"**{l.username}**" for l in links)
            await interaction.followup.send(
                f"You already have {MAX_CHARACTERS} characters linked ({names}). **/unlink** one first."
            )
            return
        if existing is not None:
            try:
                self.bot.api.forget(self.bot.cipher.decrypt(existing.token_enc))
            except TokenDecryptError:
                pass

        await self.bot.db.save_link(interaction.user.id, character_id, self.bot.cipher.encrypt(token), me.get("username"))
        await self.bot.db.save_snapshot(interaction.user.id, me)
        log.info("Discord user %s linked Koruxa character %s", interaction.user.id, character_id)

        count = len(links) + (0 if existing else 1)
        name = me.get("username", "your character")
        lines = [
            (f"🔄 Updated the token for **{name}**." if existing else f"✅ Linked **{name}**")
            + (f" ({_clan_line(me)})" if _clan_line(me) and not existing else "")
            + ("" if existing else f". That's {count}/{MAX_CHARACTERS} characters.")
        ]
        clan_tag = self.bot.clan_tag(interaction.guild_id)
        clan = me.get("clan") if isinstance(me.get("clan"), dict) else {}
        if clan_tag and str(clan.get("tag", "")).lower() != clan_tag.lower():
            lines.append(
                f"ℹ️ This character isn't in **[{clan_tag}]**, so it won't appear on the clan highscores or feed. "
                "Your own commands (/profile, /rates, /mastery, /gains …) still work for it."
            )
        else:
            lines.append("Its skill levels will show on the clan highscores and feed.")
        lines.append("Tokens are stored encrypted and only read your own data. **/unlink** removes a character.")
        if "clan_bank" not in (me.get("private_blocks") or []):
            lines.append("⚠️ This token doesn't share clan-bank data, so **/budget** won't work for it until you enable it in-game.")
        await interaction.followup.send("\n".join(lines))

    @app_commands.command(name="characters", description="List your linked Koruxa characters")
    async def characters(self, interaction: discord.Interaction) -> None:
        links = await self.bot.db.user_links(interaction.user.id)
        if not links:
            await interaction.response.send_message(NOT_LINKED, ephemeral=True)
            return
        lines = []
        for i, link in enumerate(links):
            snap = await self.bot.db.get_snapshot(link.character_id)
            clan = f"[{snap.clan_tag}]" if snap and snap.clan_tag else "no clan"
            level = f" · total level {num(snap.total_level)}" if snap and snap.total_level else ""
            lines.append(f"**{link.username}** · {clan}{level}" + (" · *default*" if i == 0 else ""))
        await interaction.response.send_message(
            f"Your linked characters ({len(links)}/{MAX_CHARACTERS}):\n" + "\n".join(lines)
            + "\n\nPick one with the `character` option on commands; without it, the default is used.",
            ephemeral=True,
        )

    @app_commands.command(name="unlink", description="Remove a linked character and delete its stored token")
    @app_commands.describe(character="Which character to unlink")
    @app_commands.autocomplete(character=character_autocomplete)
    async def unlink(self, interaction: discord.Interaction, character: str | None = None) -> None:
        links = await self.bot.db.user_links(interaction.user.id)
        if not links:
            await interaction.response.send_message("You don't have any linked characters.", ephemeral=True)
            return
        if character is None and len(links) > 1:
            await interaction.response.send_message(
                "You have several characters linked. Run **/unlink** again and pick one in the `character` option.",
                ephemeral=True,
            )
            return
        link = links[0] if character is None else next((l for l in links if str(l.character_id) == character), None)
        if link is None:
            await interaction.response.send_message("That isn't one of your linked characters.", ephemeral=True)
            return
        try:
            self.bot.api.forget(self.bot.cipher.decrypt(link.token_enc))
        except TokenDecryptError:
            pass
        await self.bot.db.delete_link(interaction.user.id, link.character_id)
        log.info("Discord user %s unlinked character %s", interaction.user.id, link.character_id)
        await interaction.response.send_message(
            f"🗑️ Unlinked **{link.username}**. Its token and tracked data have been deleted from the bot.\n"
            "To disable the token itself, revoke it in-game under **Settings > Account > API access**.",
            ephemeral=True,
        )

    @app_commands.command(name="profile", description="Show your Koruxa profile")
    @app_commands.describe(character=CHARACTER_HELP, public="Show it to everyone in the channel (default: only you)")
    @app_commands.autocomplete(character=character_autocomplete)
    async def profile(self, interaction: discord.Interaction, character: str | None = None, public: bool = False) -> None:
        linked = await self.resolve(interaction, character)
        if linked is None:
            return
        await interaction.response.defer(ephemeral=not public, thinking=True)
        me = await self.fetch_me(interaction, linked)
        if me is not None:
            await interaction.followup.send(embed=profile_embed(me))

    @app_commands.command(name="budget", description="Show your clan-bank withdrawal budget for today")
    @app_commands.describe(character=CHARACTER_HELP)
    @app_commands.autocomplete(character=character_autocomplete)
    async def budget(self, interaction: discord.Interaction, character: str | None = None) -> None:
        linked = await self.resolve(interaction, character)
        if linked is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        me = await self.fetch_me(interaction, linked)
        if me is None:
            return

        clan_bank = (me.get("private") or {}).get("clan_bank")
        if not me.get("clan"):
            await interaction.followup.send(f"**{linked.name}** isn't in a clan, so there's no clan-bank budget.")
        elif not isinstance(clan_bank, dict):
            await interaction.followup.send(
                f"**{linked.name}**'s token doesn't share clan-bank data. Enable it in the game's "
                "**Settings > Account > API access**, then try again."
            )
        else:
            await interaction.followup.send(embed=budget_embed(me, clan_bank))

    # ---------- shared helpers (also used by other cogs) ----------

    async def resolve(self, interaction: discord.Interaction, character: str | None) -> Linked | None:
        """The chosen (or default) linked character with its decrypted token, or reply with why not.

        Call before deferring: it replies with interaction.response on failure.
        """
        links = await self.bot.db.user_links(interaction.user.id)
        if not links:
            await interaction.response.send_message(NOT_LINKED, ephemeral=True)
            return None
        link = links[0] if not character else next((l for l in links if str(l.character_id) == character), None)
        if link is None:
            await interaction.response.send_message(
                "That isn't one of your linked characters. Pick one from the list, or see **/characters**.",
                ephemeral=True,
            )
            return None
        try:
            return Linked(link, self.bot.cipher.decrypt(link.token_enc))
        except TokenDecryptError:
            log.warning("Stored token for user %s character %s can't be decrypted; removing it",
                        interaction.user.id, link.character_id)
            await self.bot.db.delete_link(interaction.user.id, link.character_id)
            await interaction.response.send_message(
                f"The stored token for **{link.username}** couldn't be read (the bot's encryption key changed). "
                "Please **/link** it again.",
                ephemeral=True,
            )
            return None

    async def api_call(
        self, interaction: discord.Interaction, linked: Linked, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """GET a /me endpoint for a deferred interaction. Replies and returns None on failure."""
        try:
            return await self.bot.api.get(linked.token, path, params)
        except TokenRevoked:
            await self.bot.db.delete_link(linked.link.discord_id, linked.link.character_id)
            self.bot.api.forget(linked.token)
            log.info("Token for user %s character %s was rejected; unlinked", interaction.user.id, linked.link.character_id)
            await interaction.followup.send(
                f"🔒 Koruxa rejected the token for **{linked.name}**, so it was probably revoked. I've unlinked it.\n"
                "Create a new token in-game and use **/link** again."
            )
        except RateLimited as e:
            await interaction.followup.send(
                f"**{linked.name}**'s token has used up its API rate limit for now. Try again in about {e.retry_after:.0f} s."
            )
        except KoruxaError as e:
            log.warning("GET /%s failed for user %s: %s", path, interaction.user.id, e)
            await interaction.followup.send("Couldn't reach the Koruxa API right now. Try again shortly.")
        return None

    async def fetch_me(self, interaction: discord.Interaction, linked: Linked) -> dict[str, Any] | None:
        """GET /me for a deferred interaction and keep the character's snapshot fresh."""
        me = await self.api_call(interaction, linked, "me")
        if me is not None:
            await self.bot.db.save_snapshot(linked.link.discord_id, me)
        return me


async def setup(bot: KoruxaBot) -> None:
    await bot.add_cog(Account(bot))
