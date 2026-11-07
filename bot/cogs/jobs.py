"""Experimental: clan jobs board — named tasks members can claim.

Admins add/remove jobs; members claim/drop them. A running board lists each job
and who's on it. State in bot_state "clan_jobs" {guild_id: {seq, jobs:[...]}}.
Registers ONLY in DEMO_GUILD_ID.
"""

from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from .. import access

log = logging.getLogger(__name__)

STATE_KEY = "clan_jobs"
MAX_JOBS = 30
MAX_NAME = 60
MAX_DESC = 300


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _title(job: dict) -> str:
    emoji = _clean(job.get("emoji", ""), 32)
    name = _clean(job.get("name", ""), MAX_NAME) or "Unnamed job"
    return f"{emoji} {name}".strip()


def build_jobs_embed(jobs: list[dict], clan_name: str, is_demo: bool = False) -> discord.Embed:
    embed = discord.Embed(title=f"{clan_name} — Clan Jobs")
    if not jobs:
        embed.description = "No clan jobs yet. Admins can add one with `/job-add`."
    for job in jobs[:25]:
        assignees = job.get("assignees") or []
        who = ", ".join(f"<@{uid}>" for uid in assignees) if assignees else "*Unassigned*"
        desc = _clean(job.get("description", ""), MAX_DESC)
        value = (f"{desc}\n" if desc else "") + f"👤 {who}"
        embed.add_field(name=f"#{job.get('id')} · {_title(job)}"[:256], value=value[:1024], inline=False)
    embed.set_footer(text="Claim with /job-claim · leave with /job-drop" + (" · demo data" if is_demo else ""))
    return embed


def _demo_jobs() -> list[dict]:
    return [
        {"id": 1, "emoji": "🔥", "name": "Firepit keeper", "description": "Keep the clan firepit topped up with logs.", "assignees": [111]},
        {"id": 2, "emoji": "💰", "name": "Bank organiser", "description": "Sort deposits into the right tabs weekly.", "assignees": []},
        {"id": 3, "emoji": "⚔️", "name": "Boss caller", "description": "Rally members for clan boss spawns.", "assignees": [222, 333]},
        {"id": 4, "emoji": "🧪", "name": "Potion brewer", "description": "Stock the bank with Thieving Potions.", "assignees": []},
    ]


class Jobs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _state(self) -> dict:
        return await self.bot.db.get_state(STATE_KEY) or {}

    async def _jobs(self, guild_id: int | None) -> list[dict]:
        return (await self._state()).get(str(guild_id), {}).get("jobs", [])

    async def _save(self, guild_id: int | None, jobs: list[dict], seq: int) -> None:
        state = await self._state()
        state[str(guild_id)] = {"seq": seq, "jobs": jobs}
        await self.bot.db.set_state(STATE_KEY, state)

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        return interaction.guild is not None and interaction.user.guild_permissions.manage_guild

    def _find(self, jobs: list[dict], ref: str) -> dict | None:
        ref = ref.strip().lstrip("#")
        if ref.isdigit():
            for j in jobs:
                if str(j.get("id")) == ref:
                    return j
        low = ref.casefold()
        for j in jobs:
            if _clean(j.get("name", ""), MAX_NAME).casefold() == low:
                return j
        return None

    async def _job_ac(self, interaction: discord.Interaction, current: str):
        jobs = await self._jobs(interaction.guild_id)
        cur = current.casefold()
        out = [j for j in jobs if cur in _title(j).casefold()]
        return [app_commands.Choice(name=f"#{j['id']} {_title(j)}"[:100], value=str(j["id"])) for j in out[:25]]

    @app_commands.command(name="jobs", description="Show the clan jobs board")
    async def jobs(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        clan = self.bot.clan_tag(interaction.guild_id) or "Your clan"  # type: ignore[attr-defined]
        jobs = await self._jobs(interaction.guild_id)
        demo = not jobs
        await interaction.followup.send(embed=build_jobs_embed(jobs or _demo_jobs(), clan, is_demo=demo))

    @app_commands.command(name="job-add", description="Add a clan job (admins)")
    @app_commands.describe(name="Job name", description="What the job involves", emoji="Optional emoji")
    async def job_add(self, interaction: discord.Interaction, name: str,
                      description: str | None = None, emoji: str | None = None) -> None:
        if not await access.can_use(self.bot, interaction, "jobs"):
            await access.deny(interaction, "jobs")
            return
        jobs = await self._jobs(interaction.guild_id)
        if len(jobs) >= MAX_JOBS:
            await interaction.response.send_message(f"Job limit reached ({MAX_JOBS}). Remove one first.", ephemeral=True)
            return
        state = await self._state()
        seq = state.get(str(interaction.guild_id), {}).get("seq", 0) + 1
        jobs = jobs + [{"id": seq, "emoji": _clean(emoji or "", 32), "name": _clean(name, MAX_NAME),
                        "description": _clean(description or "", MAX_DESC), "assignees": []}]
        await self._save(interaction.guild_id, jobs, seq)
        await interaction.response.send_message(f"Added job **#{seq} {_title(jobs[-1])}**.", ephemeral=True)

    @app_commands.command(name="job-remove", description="Remove a clan job (admins)")
    @app_commands.describe(job="Job to remove")
    @app_commands.autocomplete(job=_job_ac)
    async def job_remove(self, interaction: discord.Interaction, job: str) -> None:
        if not await access.can_use(self.bot, interaction, "jobs"):
            await access.deny(interaction, "jobs")
            return
        jobs = await self._jobs(interaction.guild_id)
        target = self._find(jobs, job)
        if not target:
            await interaction.response.send_message(f"No job matching **{job}**.", ephemeral=True)
            return
        seq = (await self._state()).get(str(interaction.guild_id), {}).get("seq", 0)
        await self._save(interaction.guild_id, [j for j in jobs if j is not target], seq)
        await interaction.response.send_message(f"Removed **{_title(target)}**.", ephemeral=True)

    async def _toggle(self, interaction: discord.Interaction, job: str, claim: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        jobs = await self._jobs(interaction.guild_id)
        target = self._find(jobs, job)
        if not target:
            await interaction.followup.send(f"No job matching **{job}**.", ephemeral=True)
            return
        uid = interaction.user.id
        assignees = [a for a in target.get("assignees", []) if a != uid]
        if claim:
            assignees.append(uid)
        target["assignees"] = assignees
        seq = (await self._state()).get(str(interaction.guild_id), {}).get("seq", 0)
        await self._save(interaction.guild_id, jobs, seq)
        verb = "claimed" if claim else "left"
        await interaction.followup.send(f"You {verb} **{_title(target)}**.", ephemeral=True)

    @app_commands.command(name="job-claim", description="Claim a clan job")
    @app_commands.describe(job="Job to claim")
    @app_commands.autocomplete(job=_job_ac)
    async def job_claim(self, interaction: discord.Interaction, job: str) -> None:
        await self._toggle(interaction, job, claim=True)

    @app_commands.command(name="job-drop", description="Leave a clan job")
    @app_commands.describe(job="Job to leave")
    @app_commands.autocomplete(job=_job_ac)
    async def job_drop(self, interaction: discord.Interaction, job: str) -> None:
        await self._toggle(interaction, job, claim=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Jobs(bot))
