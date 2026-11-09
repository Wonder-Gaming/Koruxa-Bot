"""Skill focus: clan members set the skills they focus on (optionally per linked
character), and a board shows who covers each skill. Replaces the old clan-jobs
board. State in bot_state "skill_focus":
  {guild: {entity_key: {skills:[...], owner: discord_id, name: char_name|None, kind: "char"|"user"}}}
where entity_key is "c<character_id>" for a character or "u<discord_id>" for a member.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from .. import access
from ..skills import SKILL_EMOJI, SKILL_GROUPS, skill_emoji, skill_name

log = logging.getLogger(__name__)

STATE_KEY = "skill_focus"
ALL_SKILLS = [k for _, _, keys in SKILL_GROUPS for k in keys]


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


class SkillFocus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _all(self) -> dict:
        return await self.bot.db.get_state(STATE_KEY) or {}

    async def _entities(self, guild_id: int | None) -> dict:
        """Entities for a guild, migrating any old {user_id: [skills]} rows."""
        out: dict[str, dict] = {}
        for key, val in (await self._all()).get(str(guild_id), {}).items():
            if isinstance(val, list):  # legacy per-user list
                out[f"u{key}"] = {"skills": val, "owner": int(key), "name": None, "kind": "user"}
            elif isinstance(val, dict):
                out[key] = val
        return out

    async def _save(self, guild_id: int | None, entities: dict) -> None:
        state = await self._all()
        state[str(guild_id)] = entities
        await self.bot.db.set_state(STATE_KEY, state)

    def _resolve_skill(self, value: str) -> str | None:
        v = _norm(value)
        if v in SKILL_EMOJI:
            return v
        return next((k for k in ALL_SKILLS if skill_name(k).lower() == v), None)

    async def _resolve_entity(self, owner: discord.abc.User, character: str | None):
        """(entity_key, name, kind) for a chosen linked character, or the member."""
        if character:
            for ln in await self.bot.db.user_links(owner.id):
                if str(ln.character_id) == str(character) or _norm(ln.username) == _norm(character):
                    return f"c{ln.character_id}", (ln.username or f"Char {ln.character_id}"), "char"
            return None  # not one of their characters
        return f"u{owner.id}", None, "user"

    async def _skill_ac(self, interaction: discord.Interaction, current: str):
        cur = _norm(current)
        hits = [k for k in ALL_SKILLS if cur in k or cur in skill_name(k).lower()]
        return [app_commands.Choice(name=f"{skill_emoji(k)} {skill_name(k)}"[:100], value=k) for k in hits[:25]]

    async def _char_ac(self, interaction: discord.Interaction, current: str):
        target = getattr(interaction.namespace, "member", None)
        uid = target.id if target else interaction.user.id
        cur = _norm(current)
        links = [ln for ln in await self.bot.db.user_links(uid) if cur in _norm(ln.username)]
        return [app_commands.Choice(name=(ln.username or f"Char {ln.character_id}")[:100],
                                    value=str(ln.character_id)) for ln in links[:25]]

    @staticmethod
    def _render(ent: dict) -> str:
        if ent.get("kind") == "char" and ent.get("name"):
            return f"**{ent['name']}** (<@{ent['owner']}>)"
        return f"<@{ent['owner']}>"

    def build_board(self, entities: dict, clan: str) -> discord.Embed:
        by_skill: dict[str, list[dict]] = {}
        for ent in entities.values():
            for s in ent.get("skills", []):
                by_skill.setdefault(s, []).append(ent)
        embed = discord.Embed(title=f"🎯 {clan} — Skill Focus", colour=discord.Colour.green())
        blocks = []
        for label, gemoji, keys in SKILL_GROUPS:
            lines = []
            for k in keys:
                ents = by_skill.get(k)
                if ents:
                    lines.append(f"{skill_emoji(k)} **{skill_name(k)}** — " + ", ".join(self._render(e) for e in ents))
            if lines:
                # Markdown "##" headers render large, so the groups clearly stand apart.
                blocks.append(f"## {label}\n" + "\n".join(lines))
        if blocks:
            embed.description = "\n\n".join(blocks)[:4096]
        else:
            embed.description = "No one has set their focus skills yet. Use `/focus <skill>`."
        embed.set_footer(text="Set yours with /focus (optionally per character) · officers: /focus-set")
        return embed

    async def _apply(self, guild_id, owner, character, key, *, force: bool | None):
        ent_id = await self._resolve_entity(owner, character)
        if ent_id is None:
            return None
        entity_key, name, kind = ent_id
        entities = await self._entities(guild_id)
        ent = entities.get(entity_key) or {"skills": [], "owner": owner.id, "name": name, "kind": kind}
        skills = set(ent.get("skills", []))
        if force is True:
            skills.add(key); added = True
        elif force is False:
            skills.discard(key); added = False
        else:
            added = key not in skills
            skills.add(key) if added else skills.discard(key)
        ent["skills"] = sorted(skills, key=ALL_SKILLS.index)
        ent.update(owner=owner.id, name=name, kind=kind)
        if ent["skills"]:
            entities[entity_key] = ent
        else:
            entities.pop(entity_key, None)
        await self._save(guild_id, entities)
        return added, name

    @app_commands.command(name="focus", description="Show the skill-focus board, or toggle a skill you focus on")
    @app_commands.describe(skill="A skill to toggle (leave empty to show the board)",
                           character="Attribute it to one of your linked characters (optional)")
    @app_commands.autocomplete(skill=_skill_ac, character=_char_ac)
    async def focus(self, interaction: discord.Interaction, skill: str | None = None,
                    character: str | None = None) -> None:
        if skill is None:
            await interaction.response.defer()
            clan = self.bot.clan_tag(interaction.guild_id) or "Clan"  # type: ignore[attr-defined]
            await interaction.followup.send(embed=self.build_board(await self._entities(interaction.guild_id), clan))
            return
        key = self._resolve_skill(skill)
        if not key:
            await interaction.response.send_message(f"Unknown skill: **{skill}**.", ephemeral=True)
            return
        result = await self._apply(interaction.guild_id, interaction.user, character, key, force=None)
        if result is None:
            await interaction.response.send_message("That isn't one of your linked characters.", ephemeral=True)
            return
        added, name = result
        who = f" on **{name}**" if name else ""
        word = "now focusing" if added else "no longer focusing"
        await interaction.response.send_message(
            f"You're {word} {skill_emoji(key)} **{skill_name(key)}**{who}.", ephemeral=True)

    @app_commands.command(name="focus-set", description="Assign or unassign a member's focus skill (admins)")
    @app_commands.describe(member="Member", skill="Skill", character="Their linked character (optional)",
                           remove="Unassign instead of assign")
    @app_commands.autocomplete(skill=_skill_ac, character=_char_ac)
    async def focus_set(self, interaction: discord.Interaction, member: discord.Member, skill: str,
                        character: str | None = None, remove: bool = False) -> None:
        if not await access.can_use(self.bot, interaction, "focus"):
            await access.deny(interaction, "focus")
            return
        key = self._resolve_skill(skill)
        if not key:
            await interaction.response.send_message(f"Unknown skill: **{skill}**.", ephemeral=True)
            return
        result = await self._apply(interaction.guild_id, member, character, key, force=not remove)
        if result is None:
            await interaction.response.send_message(f"That isn't one of {member.mention}'s linked characters.", ephemeral=True)
            return
        _, name = result
        who = f" ({name})" if name else ""
        did = "Unassigned" if remove else "Assigned"
        await interaction.response.send_message(
            f"{did} {member.mention}{who}: {skill_emoji(key)} **{skill_name(key)}**.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SkillFocus(bot))
