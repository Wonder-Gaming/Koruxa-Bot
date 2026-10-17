"""/rates: your best XP/hour per action, from the API's exact per-action timing,
with optional "what if" boosts.

/me/actions reflects the player's current gear, tools, buffs and boosts. It is
fetched for one skill at a time (?skill=, ~8 KB instead of ~430 KB) and the
server caches it for 10 minutes (no parameter bypasses that), so the footer
shows when the numbers were generated.

What-if mode rebuilds each action's speed from the API's published formula:
  duration = raw x max(1 - cap/100, 1 - speed/100) x (1 + penalty/100), >= 1000 ms
  speed    = permanent (gear, tools, perks) + well boosts + strongest personal buff + other temporary
Personal buffs (potions, food, Daily Brew) don't stack: the strongest per type
counts. The API only gives one combined XP%, so changed XP is an estimate.

Capes: the API (like the game's own numbers) uses the cape worn right now. If
you own a better cape for the skill, the reply says so, and cape:True swaps the
worn cape's bonus for the best owned one (checked to match the in-game XP exactly).

/mastery plans the quickest route to a total mastery level (see bot/mastery.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from ..fmt import compact, timestamp
from .account import CHARACTER_HELP, character_autocomplete
from ..mastery import MASTERY_GATES, duration_text, plan
from ..skills import SKILL_SOURCE, skill_label, skill_name

if TYPE_CHECKING:
    from ..client import KoruxaBot
    from .account import Account

# Skills that have actions in /me/actions (farming and combat don't).
RATE_SKILLS = [
    "woodcutting", "mining", "fishing", "thieving", "cooking", "fletching", "crafting", "jewelery",
    "herblore", "smithing", "firemaking", "alchemy", "arcana", "construction", "tinkering",
]
SKILL_CHOICES = [app_commands.Choice(name=skill_name(k), value=k) for k in RATE_SKILLS]
UNLOCK_CHOICES = [
    app_commands.Choice(name="3,000 (unlocks level 120 actions)", value=3000),
    app_commands.Choice(name="3,500 (unlocks level 135 actions)", value=3500),
    app_commands.Choice(name="4,000 (unlocks level 150 actions)", value=4000),
    app_commands.Choice(name="All three", value=0),
]
SORT_CHOICES = [
    app_commands.Choice(name="XP per hour", value="xp"),
    app_commands.Choice(name="Mastery XP per hour", value="mastery"),
]
SHOW = 8
INDENT = "​ "  # see highscores.py


# ---------- boosts ----------

@dataclass
class Boosts:
    wells_speed: float = 0.0
    wells_xp: float = 0.0
    personal_speed: float = 0.0  # strongest active personal speed buff
    personal_xp: float = 0.0
    brew_speed: float = 25.0
    brew_xp: float = 25.0
    known: bool = False  # the token shares the boosts block


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read_boosts(me: dict[str, Any] | None) -> Boosts:
    boosts = ((me or {}).get("private") or {}).get("boosts")
    if not isinstance(boosts, dict):
        return Boosts()
    b = Boosts(known=True)
    for g in boosts.get("global_boosts") or []:
        kind = str(g.get("type", "")).lower()
        if "speed" in kind:
            b.wells_speed += _num(g.get("value"))
        elif kind.startswith("xp") or kind.endswith("xp_boost"):
            b.wells_xp += _num(g.get("value"))
    # Shape not seen yet (no potion was active when this was written): read it defensively.
    for p in boosts.get("potions") or []:
        if not isinstance(p, dict):
            continue
        kind = " ".join(str(p.get(k, "")) for k in ("type", "kind", "stat", "key")).lower()
        value = _num(p.get("value", p.get("pct", p.get("bonus"))))
        if "speed" in kind:
            b.personal_speed = max(b.personal_speed, value)
        if "xp" in kind:
            b.personal_xp = max(b.personal_xp, value)
    brew = (boosts.get("patron") or {}).get("daily_brew") or {}
    b.brew_speed = _num(brew.get("speed_pct")) or b.brew_speed
    b.brew_xp = _num(brew.get("xp_pct")) or b.brew_xp
    return b


@dataclass
class CapeFix:
    xp: float = 0.0  # percentage points to add to the API's XP%
    speed: float = 0.0  # percentage points to add to the API's speed
    name: str | None = None  # the cape auto-cape would wear

    @property
    def active(self) -> bool:
        return bool(self.xp or self.speed)


def cape_fix(me: dict[str, Any] | None, skill: str) -> CapeFix:
    capes = ((me or {}).get("private") or {}).get("capes")
    if not isinstance(capes, dict):
        return CapeFix()
    owned = [c for c in capes.get("owned") or [] if isinstance(c, dict)]
    worn = next((c for c in owned if c.get("worn") or c.get("item_key") == capes.get("worn")), None)
    mine = [c for c in owned if c.get("skill") == skill]
    if not mine:
        return CapeFix()
    best = max(mine, key=lambda c: (_num(c.get("score")), _num(c.get("xp")), _num(c.get("speed"))))
    worn_counts = worn is not None and worn.get("skill") == skill
    return CapeFix(
        xp=_num(best.get("xp")) - (_num(worn.get("xp")) if worn_counts else 0.0),
        speed=_num(best.get("speed")) - (_num(worn.get("speed")) if worn_counts else 0.0),
        name=str(best.get("name") or best.get("item_key")),
    )


@dataclass
class Scenario:
    wells: bool
    potions: bool
    brew: bool
    extra_speed: int
    extra_xp: int
    clan_boosts: bool = True  # temporary speed the API doesn't itemise (e.g. clan boosts)

    @property
    def is_current(self) -> bool:
        return self.wells and self.potions and not self.brew and not self.extra_speed and not self.extra_xp


def simulate(action: dict[str, Any], b: Boosts, s: Scenario, cape: CapeFix | None = None) -> tuple[float, float, bool]:
    """(duration ms, xp per cycle, xp_is_estimate) for an action under scenario `s`."""
    cape = cape or CapeFix()
    speed = action.get("speed") or {}
    if (s.is_current and not cape.active) or not action.get("raw_duration_ms"):
        return _num(action.get("duration_ms")), _num(action.get("xp_per_cycle")), False

    total, permanent = _num(speed.get("total_pct")), _num(speed.get("permanent_pct"))
    other = max(0.0, total - permanent - b.wells_speed - b.personal_speed) if s.clan_boosts else 0.0
    if s.is_current:
        wells_speed, personal_speed = b.wells_speed, b.personal_speed
    else:
        wells_speed = b.wells_speed if s.wells else 0.0
        personal_speed = max(b.personal_speed if s.potions else 0.0, b.brew_speed if s.brew else 0.0, float(s.extra_speed))
    new_speed = permanent + cape.speed + other + wells_speed + personal_speed
    cap = _num(speed.get("cap_pct")) or 100.0
    duration = _num(action["raw_duration_ms"]) * max(1 - cap / 100, 1 - new_speed / 100)
    duration *= 1 + _num(speed.get("penalty_pct")) / 100
    duration = max(duration, 1000.0, _num(speed.get("floor_ms")))

    xp_pct = _num(action.get("xp_pct"))
    if s.is_current:
        new_xp_pct, estimate = xp_pct + cape.xp, False
    else:
        personal_xp = max(b.personal_xp if s.potions else 0.0, b.brew_xp if s.brew else 0.0, float(s.extra_xp))
        new_xp_pct = xp_pct + cape.xp - b.wells_xp - b.personal_xp + (b.wells_xp if s.wells else 0.0) + personal_xp
        estimate = new_xp_pct != xp_pct + cape.xp
    xp_cycle = _num(action.get("xp_per_cycle")) * (1 + new_xp_pct / 100) / (1 + xp_pct / 100)
    return duration, xp_cycle, estimate


class _NoTemporaryBoosts(Scenario):
    """Always simulated, even though no boost is added."""

    @property
    def is_current(self) -> bool:
        return False


def _per_hour(per_cycle: float, duration_ms: float) -> float:
    return per_cycle * 3_600_000 / duration_ms if duration_ms else 0.0


# ---------- embed ----------

def _pct(v: float) -> str:
    return f"+{v:g}%"


def scenario_line(b: Boosts, s: Scenario) -> str:
    if s.is_current:
        return (
            f"**As now:** wells {_pct(b.wells_speed)} speed / {_pct(b.wells_xp)} XP · "
            f"personal buffs {_pct(b.personal_speed)} speed / {_pct(b.personal_xp)} XP"
        )
    parts = [
        f"wells {'✅' if s.wells else '❌'}",
        f"your active buffs {'✅' if s.potions else '❌'}",
        f"Daily Brew {'✅ ' + _pct(b.brew_speed) + ' speed/' + _pct(b.brew_xp) + ' XP' if s.brew else '❌'}",
    ]
    if s.extra_speed or s.extra_xp:
        parts.append(f"extra {_pct(s.extra_speed)} speed / {_pct(s.extra_xp)} XP")
    return "**What if:** " + " · ".join(parts)


def rates_embed(
    skill: str,
    data: dict[str, Any],
    sort: str,
    boosts: Boosts,
    scenario: Scenario,
    cape: CapeFix | None = None,
    use_cape: bool = False,
) -> discord.Embed:
    available = cape or CapeFix()
    cape = available if use_cape else CapeFix()
    actions = [a for a in data.get("actions") or [] if isinstance(a, dict)]
    rows = []
    estimate = False
    for a in actions:
        duration, xp_cycle, xp_est = simulate(a, boosts, scenario, cape)
        estimate |= xp_est
        rows.append((a, duration, _per_hour(xp_cycle, duration), _per_hour(_num(a.get("mastery_xp_per_cycle")), duration)))
    unlocked = [r for r in rows if r[0].get("unlocked")]
    unlocked.sort(key=lambda r: -(r[2] if sort == "xp" else r[3]))

    lines = [
        f"**{a.get('label', a.get('action', '?'))}** · lvl {a.get('level_req', '?')}\n"
        f"{INDENT}⏱ {duration / 1000:.1f}s · ⭐ **{compact(xp_h)}** XP/h · 📘 {compact(m_h)} mastery/h"
        for a, duration, xp_h, m_h in unlocked[:SHOW]
    ]
    embed = discord.Embed(
        title=f"{skill_label(skill)} rates",
        description=(
            scenario_line(boosts, scenario) + "\n"
            + (f"🧥 Counting **{cape.name}** ({_pct(cape.xp)} XP"
               + (f", {_pct(cape.speed)} speed" if cape.speed else "") + " over the cape you're wearing)\n"
               if cape.active else "")
            + (f"🧥 Tip: wearing your **{available.name}** would add {_pct(available.xp)} XP"
               + (f" / {_pct(available.speed)} speed" if available.speed else "") + ". Add `cape:True` to see it.\n"
               if available.active and not use_cape else "")
            + 
            f"Best {min(SHOW, len(unlocked))} of your {len(unlocked)} unlocked actions, by "
            f"{'XP' if sort == 'xp' else 'mastery XP'} per hour.\n\n" + ("\n".join(lines) or "No unlocked actions.")
        ),
        colour=discord.Colour.orange(),
        timestamp=timestamp(data.get("generated_at")),
    )

    if unlocked and scenario.is_current:
        a = unlocked[0][0]
        duration, xp_cycle, _ = simulate(a, boosts, _NoTemporaryBoosts(False, False, False, 0, 0, clan_boosts=False), cape)
        base = _per_hour(xp_cycle, duration)
        if base and abs(base - unlocked[0][2]) >= 1:
            embed.add_field(
                name="Without temporary boosts",
                value=f"{a.get('label')}: {compact(base)} XP/h (no potions, brew, wells or clan boosts)",
                inline=False,
            )
    locked = sorted((r for r in rows if not r[0].get("unlocked")), key=lambda r: r[0].get("level_req") or 0)
    if locked:
        a, _, xp_h, _ = locked[0]
        embed.add_field(name="Next unlock", value=f"{a.get('label')} at level {a.get('level_req')}: {compact(xp_h)} XP/h", inline=False)

    notes = ["Your gear and tools now"]
    if estimate:
        notes.insert(0, "XP with changed boosts is an estimate ·")
    if not boosts.known and not scenario.is_current:
        notes.insert(0, "Your token doesn't share boosts, so wells/buffs are unknown ·")
    embed.set_footer(text=" ".join(notes) + " (the game caches this for 10 min)")
    return embed


def mastery_embed(skill: str, current: int, milestones: list, data: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=f"{skill_label(skill)} mastery plan",
        description=(
            f"Total mastery now: **{current:,}**\n"
            "Train each action up to the mastery level shown. The levels are picked from your permanent "
            "rates, so they don't change when boosts come and go; times are with your boosts now."
        ),
        colour=discord.Colour(0xA855F7),
        timestamp=timestamp(data.get("generated_at")),
    )
    for m in milestones:
        if m.target <= current:
            embed.add_field(name=f"🎯 {m.target:,}", value="Already reached ✅", inline=False)
            continue
        if not m.reached:
            embed.add_field(name=f"🎯 {m.target:,}", value="Not reachable with your unlocked actions.", inline=False)
            continue
        head = f"~**{duration_text(m.hours_now)}** now · {duration_text(m.hours_base)} without temporary boosts"
        rows = sorted(m.gains.values(), key=lambda g: -g.hours_now)
        lines = [
            f"{INDENT}{g.label} → **M{g.end}** (+{g.end - g.start}) · {duration_text(g.hours_now)}"
            + (f" *(after {g.gate:,})*" if g.gate else "")
            for g in rows
        ]
        tail = ""
        # Only worth mentioning when it costs little extra time (one action gets slow at high levels).
        if m.single and len(m.gains) > 1 and m.single.hours_now <= m.hours_now * 1.25:
            extra = duration_text(max(m.single.hours_now - m.hours_now, 0))
            tail = (
                f"\n{INDENT}*Simplest: {m.single.label} only → M{m.single.end} · "
                f"{duration_text(m.single.hours_now)} (+{extra}) · {duration_text(m.single.hours_base)} without boosts*"
            )
        value = head + "\n" + "\n".join(lines) + tail
        if len(value) > 1024:  # very many actions: keep the biggest ones
            while len(value) > 1000 - len(tail):
                lines.pop()
                value = head + "\n" + "\n".join(lines)
            value += f"\n{INDENT}…and more" + tail
        embed.add_field(name=f"🎯 {m.target:,} (+{m.target - current:,})", value=value, inline=False)
    embed.set_footer(
        text="Active training time; ignores switching and offline limits. "
        "Level 120/135/150 actions unlock at 3,000/3,500/4,000 total mastery · data as of"
    )
    return embed


# ---------- the cog ----------

def fix_unlocks(skill: str, data: dict[str, Any], me: dict[str, Any]) -> None:
    """For a skill the API has no level for (Alchemy uses the Magic level in game), it marks every action
    locked. Work "unlocked" out from the source skill's level instead (it only checks the level)."""
    source = SKILL_SOURCE.get(skill)
    if source is None:
        return
    level = next((int(sk.get("level") or 0) for sk in me.get("skills") or []
                  if isinstance(sk, dict) and str(sk.get("skill_key")).lower() == source), 0)
    for a in data.get("actions") or []:
        if isinstance(a, dict) and isinstance(a.get("level_req"), int):
            a["unlocked"] = a["level_req"] <= level


class Rates(commands.Cog):
    def __init__(self, bot: KoruxaBot):
        self.bot = bot

    @app_commands.command(name="rates", description="Your best XP per hour for a skill, with optional what-if boosts")
    @app_commands.describe(
        skill="Which skill",
        sort="Rank by XP or mastery XP (default: XP)",
        wells="Include the server-wide well boosts (default: as now)",
        buffs="Include your currently active potions/food/brew (default: as now)",
        brew="Add a Daily Brew (default: no)",
        extra_speed="Another speed buff to try, in % (e.g. a potion you plan to drink)",
        extra_xp="Another XP buff to try, in %",
        cape="Count your best owned cape for this skill instead of the one you're wearing",
        character=CHARACTER_HELP,
        public="Show it to everyone in the channel (default: only you)",
    )
    @app_commands.choices(skill=SKILL_CHOICES, sort=SORT_CHOICES)
    @app_commands.autocomplete(character=character_autocomplete)
    async def rates(
        self,
        interaction: discord.Interaction,
        skill: app_commands.Choice[str],
        sort: app_commands.Choice[str] | None = None,
        wells: bool = True,
        buffs: bool = True,
        brew: bool = False,
        extra_speed: app_commands.Range[int, 0, 300] = 0,
        extra_xp: app_commands.Range[int, 0, 500] = 0,
        cape: bool = False,
        character: str | None = None,
        public: bool = False,
    ) -> None:
        account: Account | None = self.bot.get_cog("Account")  # type: ignore[assignment]
        linked = await account.resolve(interaction, character) if account else None
        if linked is None:
            return
        await interaction.response.defer(ephemeral=not public, thinking=True)
        data = await account.api_call(interaction, linked, "me/actions", {"skill": skill.value})
        if data is None:
            return
        me = await account.fetch_me(interaction, linked)  # boosts; usually served from cache
        if me is None:
            return
        fix_unlocks(skill.value, data, me)
        scenario = Scenario(wells, buffs, brew, extra_speed, extra_xp)
        embed = rates_embed(
            skill.value, data, sort.value if sort else "xp", read_boosts(me), scenario,
            cape_fix(me, skill.value), use_cape=cape,
        )
        embed.title = f"{embed.title} · {linked.name}"
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="mastery", description="Quickest route to the total mastery that unlocks a skill's expert actions")
    @app_commands.describe(
        unlock="Which mastery requirement to plan for",
        skill="Which skill",
        character=CHARACTER_HELP,
        public="Show it to everyone in the channel (default: only you)",
    )
    @app_commands.choices(unlock=UNLOCK_CHOICES, skill=SKILL_CHOICES)
    @app_commands.autocomplete(character=character_autocomplete)
    async def mastery(
        self,
        interaction: discord.Interaction,
        unlock: app_commands.Choice[int],
        skill: app_commands.Choice[str],
        character: str | None = None,
        public: bool = False,
    ) -> None:
        account: Account | None = self.bot.get_cog("Account")  # type: ignore[assignment]
        linked = await account.resolve(interaction, character) if account else None
        if linked is None:
            return
        await interaction.response.defer(ephemeral=not public, thinking=True)
        data = await account.api_call(interaction, linked, "me/actions", {"skill": skill.value})
        if data is None:
            return
        me = await account.fetch_me(interaction, linked)
        if me is None:
            return
        fix_unlocks(skill.value, data, me)
        mastery_rows = ((me.get("private") or {}).get("mastery") or {}).get("actions")
        if not isinstance(mastery_rows, list):
            await interaction.followup.send(
                "Your token doesn't share mastery data. Enable **mastery** in the game's "
                "**Settings > Account > API access**, then try again."
            )
            return
        targets = [unlock.value] if unlock.value else [total for _, total in sorted(MASTERY_GATES)]
        current, milestones = plan(data.get("actions") or [], mastery_rows, skill.value, targets)
        embed = mastery_embed(skill.value, current, milestones, data)
        embed.title = f"{embed.title} · {linked.name}"
        await interaction.followup.send(embed=embed)


async def setup(bot: KoruxaBot) -> None:
    await bot.add_cog(Rates(bot))
