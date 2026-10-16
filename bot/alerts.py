"""Posting alerts to each server's configured channel, with the optional role ping."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .client import KoruxaBot

log = logging.getLogger(__name__)


def channel_problems(channel: discord.abc.GuildChannel, role: discord.Role | None) -> list[str]:
    """Permission problems that would stop alerts in `channel` (or their ping) from working."""
    perms = channel.permissions_for(channel.guild.me)
    missing = [
        name
        for name, ok in (
            ("View Channel", perms.view_channel),
            ("Send Messages", perms.send_messages),
            ("Embed Links", perms.embed_links),
        )
        if not ok
    ]
    problems = []
    if missing:
        problems.append(f"I'm missing **{', '.join(missing)}** in {channel.mention}.")
    if role is not None and not role.mentionable and not perms.mention_everyone:
        problems.append(
            f"{role.mention} isn't mentionable and I don't have **Mention @everyone, @here and All Roles** "
            f"in {channel.mention}, so pings won't notify anyone."
        )
    return problems


async def post_alert(
    bot: KoruxaBot,
    feature: str,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    ping: bool = True,
    guild_id: int | None = None,
    image: bytes | None = None,
    filename: str = "image.png",
) -> dict[int, discord.Message]:
    """Post to every server's channel for `feature` (or just `guild_id`). Returns the messages by server.

    `image` is PNG bytes attached to the message (a fresh File per server, since a File can only be sent once).
    """
    sent: dict[int, discord.Message] = {}
    for cfg in await bot.db.feature_targets(feature):
        if guild_id is not None and cfg.guild_id != guild_id:
            continue
        channel = bot.get_channel(cfg.channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("%s channel %s in guild %s is gone or not a text channel", feature, cfg.channel_id, cfg.guild_id)
            continue
        role = channel.guild.get_role(cfg.role_id) if ping and cfg.role_id else None
        text = " ".join(part for part in (role.mention if role else None, content) if part) or None
        try:
            sent[cfg.guild_id] = await channel.send(
                content=text,
                embed=embed,
                file=discord.File(io.BytesIO(image), filename=filename) if image else discord.utils.MISSING,
                allowed_mentions=discord.AllowedMentions(roles=[role]) if role else discord.AllowedMentions.none(),
            )
        except discord.HTTPException as e:
            log.warning("Couldn't post %s alert in guild %s: %s", feature, cfg.guild_id, e)
    return sent


async def update_status(bot: KoruxaBot, feature: str, embed: discord.Embed) -> None:
    """Edit each server's live status message for `feature`, posting one where there isn't one yet."""
    for cfg in await bot.db.feature_targets(feature):
        channel = bot.get_channel(cfg.channel_id)
        if not isinstance(channel, discord.TextChannel):
            continue
        if cfg.status_message_id:
            try:
                await channel.get_partial_message(cfg.status_message_id).edit(embed=embed)
                continue
            except discord.NotFound:
                pass  # deleted: post a new one below
            except discord.HTTPException as e:
                log.warning("Couldn't edit %s status in guild %s: %s", feature, cfg.guild_id, e)
                continue
        try:
            message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.warning("Couldn't post %s status in guild %s: %s", feature, cfg.guild_id, e)
            continue
        await bot.db.set_feature_field(cfg.guild_id, feature, "status_message_id", message.id)


async def reset_status(bot: KoruxaBot, feature: str, messages: dict[int, discord.Message] | None = None) -> None:
    """Forget the status messages (so the next update posts fresh ones), or adopt `messages` as them."""
    for cfg in await bot.db.feature_targets(feature):
        message = (messages or {}).get(cfg.guild_id)
        await bot.db.set_feature_field(cfg.guild_id, feature, "status_message_id", message.id if message else None)
