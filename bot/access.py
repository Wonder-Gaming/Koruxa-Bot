"""Per-command access control for experimental admin commands.

Each gated command belongs to a SCOPE. A scope's access mode is one of:
  managers  — only Manage Server (the default)
  everyone  — anyone
  custom    — Manage Server, OR a listed Discord role, OR a listed IN-GAME clan rank

In-game rank is the improvement over the reference bot (Discord-roles only): a
user's linked characters' clan ranks (from /clan-xp members) are checked against
the scope's allowed ranks. Config lives in bot_state "access"
{guild_id: {scope: {mode, roles:[ids], ranks:[slugs]}}}.
"""

from __future__ import annotations

from typing import Any

import discord

STATE_KEY = "access"
DEFAULT_MODE = "managers"

SCOPES: dict[str, str] = {
    "bank": "Bank stock targets — /stock-set, /stock-remove",
    "focus": "Skill focus — /focus-set",
    "wiki": "Wiki cache — /wiki-sync",
    "guides": "Guides — /guides publish",
}


async def get_config(bot, guild_id: int | None, scope: str) -> dict[str, Any]:
    return (await bot.db.get_state(STATE_KEY) or {}).get(str(guild_id), {}).get(scope, {})


async def set_config(bot, guild_id: int | None, scope: str, cfg: dict | None) -> None:
    state = await bot.db.get_state(STATE_KEY) or {}
    guild = state.setdefault(str(guild_id), {})
    if cfg is None:
        guild.pop(scope, None)
    else:
        guild[scope] = cfg
    await bot.db.set_state(STATE_KEY, state)


async def _clan_members(bot, guild_id: int | None) -> list[dict]:
    cog = bot.get_cog("Clan")
    tracker = cog.tracker_for(guild_id) if cog else None
    if tracker is None:
        return []
    data = await tracker.fetch("clan-xp", {"detail": "members"})
    return (data or {}).get("members") or []


async def rank_names(bot, guild_id: int | None) -> dict[str, str]:
    """slug -> display name for this clan's in-game ranks."""
    out: dict[str, str] = {}
    for m in await _clan_members(bot, guild_id):
        if m.get("rank"):
            out[str(m["rank"])] = str(m.get("rank_name") or m["rank"])
    return out


async def member_ranks(bot, guild_id: int | None, discord_id: int) -> set[str]:
    """The in-game rank slugs of the user's linked characters."""
    links = await bot.db.user_links(discord_id)
    cids = {ln.character_id for ln in links}
    if not cids:
        return set()
    return {str(m["rank"]) for m in await _clan_members(bot, guild_id)
            if m.get("character_id") in cids and m.get("rank")}


async def can_use(bot, interaction: discord.Interaction, scope: str) -> bool:
    # Manage Server is always allowed (admin override).
    if interaction.guild is not None and interaction.user.guild_permissions.manage_guild:
        return True
    cfg = await get_config(bot, interaction.guild_id, scope)
    mode = cfg.get("mode", DEFAULT_MODE)
    if mode == "everyone":
        return True
    if mode == "managers":
        return False
    # custom: allowed Discord roles OR in-game ranks
    role_ids = set(cfg.get("roles", []))
    if role_ids and any(r.id in role_ids for r in getattr(interaction.user, "roles", [])):
        return True
    ranks = set(cfg.get("ranks", []))
    if ranks and (await member_ranks(bot, interaction.guild_id, interaction.user.id) & ranks):
        return True
    return False


async def deny(interaction: discord.Interaction, scope: str) -> None:
    msg = f"You don't have access to this (**{scope}**). Ask an admin, or `/access show`."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)
