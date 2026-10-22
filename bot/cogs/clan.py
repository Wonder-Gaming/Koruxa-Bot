"""Clan features, read with the clan's own API token.

Each Discord server links its own clan token: an admin runs /link-clan (stored
encrypted in bot_state "guild_clans", like the personal tokens) and /clear-clan
removes it. That server's highscores, feed and /topgains count that clan, and
its clan channels get that clan's updates. Servers that link the same clan
share one ClanTracker, so each clan is polled once.

One token reads /clan-bank, /clan-xp, /clan-boss and /clan-institute, 120
requests per hour in total, with answers cached for 60 seconds. Polling uses
about 85 of those an hour, leaving the rest for commands:
- bank every 10 min: two messages edited in place, the bank summary and below
  it the latest deposits/withdrawals plus pending requests (Clan Bank channel)
- boss every minute (the cache makes faster pointless): one card edited when HP
  or the tier changes, with a Clan Boss role ping under it while the boss is
  alive, deleted once it's dead (Clan Boss channel).
  Under the card, a second one ranks who has hit this boss hardest. The API
  has no per-boss damage, so it's each member's weekly boss_damage (clan-xp
  detail=members) minus a snapshot taken when the boss spawned, updated every
  5 minutes with the XP poll's request (see track_damage)
- XP every 5 min: clan level-ups (Clan Feed channel)
- Institute every 10 min: research started and completed (Clan Feed channel)
- at the weekly reset: a clan recap card for the week just ended (Clan Feed)

So far these were only seen from a new test clan, so withdrawal requests, the
enchant queue and active research are read defensively (like
the world boss) and logged raw the first time they appear, to refine later.
Bank log entries look like {"id", "at", "action": "deposit"|"withdraw",
"character", "character_id", "item_key", "name", "quantity", "unit_value",
"value", "tab_id"}, and items like {"item_key", "name", "category", "rarity",
"quantity", "tab_id", "unit_value", "total_value"}. The live boss is described
in boss_embed(). Each tracker remembers which clan it saw: when the token is swapped for
another clan's, it takes a fresh baseline instead of announcing everything.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..alerts import post_alert
from ..api import KoruxaError, RateLimited, TokenRevoked, fingerprint
from ..crypto import TokenDecryptError
from ..fmt import bar, compact, num, timestamp, title
from ..render import (
    Activity, Announcement, BankItem, BossCard, BossPart, Contributor, DamageRow, MAX_ACTIVITY_ROWS, MAX_DAMAGE_ROWS, render_announcement, render_bank,
    render_bank_activity, render_boss, render_boss_damage, render_clan_week,
)
from .worldboss import NAME_KEYS, _display, _embed_fingerprint, _first, _flatten, _name, event_key

if TYPE_CHECKING:
    from ..client import KoruxaBot

log = logging.getLogger(__name__)

BANK_COLOUR = discord.Colour(0x3B82F6)
BOSS_COLOUR = discord.Colour(0xEF4444)
CLAN_COLOUR = discord.Colour(0x60A5FA)
INSTITUTE_COLOUR = discord.Colour(0xA855F7)
INDENT = "​ "  # see highscores.py

BANK_LOG_LIMIT = "15"  # the activity card shows the last 15 changes

TIME_KEYS = ("at", "created_at", "time", "ts", "timestamp", "date")
WHO_KEYS = ("character", "character_name", "player", "username", "member", "by", "requested_by")
ITEM_KEYS = ("item_name", "name", "item", "item_key")  # the log and items use "name" + "item_key"
QTY_KEYS = ("qty", "quantity", "amount", "count")
VALUE_KEYS = ("value", "total_value", "value_total", "items_value")
KIND_KEYS = ("type", "action", "direction", "kind", "event")
IN_WORDS = {"deposit", "deposited", "in", "add", "added", "donate", "donated"}
OUT_WORDS = {"withdraw", "withdrawal", "withdrew", "withdrawn", "out", "take", "took", "taken", "remove", "removed"}
END_KEYS = ("ends_at", "end_at", "finishes_at", "completes_at", "done_at", "end")


# ---------- reading the data ----------

def when(value: Any) -> datetime | None:
    """ISO 8601 text or a Unix timestamp (seconds or milliseconds)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value / 1000 if value > 1e12 else value, timezone.utc)
    return timestamp(value)


def item_text(entry: dict[str, Any]) -> str | None:
    item = _first(entry, ITEM_KEYS)
    if isinstance(item, dict):
        item = _first(item, ITEM_KEYS + NAME_KEYS)
    if not isinstance(item, (str, int)) or item == "":
        return None
    return title(item) if isinstance(item, str) and "_" in item else str(item)


def qty_value(entry: dict[str, Any]) -> float | None:
    qty = _first(entry, QTY_KEYS)
    return qty if isinstance(qty, (int, float)) and not isinstance(qty, bool) else None


def log_line(entry: dict[str, Any]) -> str:
    kind = str(_first(entry, KIND_KEYS) or "").lower()
    who = _name(_first(entry, WHO_KEYS))
    item, qty = item_text(entry), qty_value(entry)
    if kind in IN_WORDS or "deposit" in kind:
        emoji, verb = "📥", "deposited"
    elif kind in OUT_WORDS or "withdr" in kind or (qty is not None and qty < 0):
        emoji, verb = "📤", "withdrew"
    else:
        emoji, verb = "🔁", title(kind).lower() if kind else None
    at = when(_first(entry, TIME_KEYS))
    stamp = f" · {discord.utils.format_dt(at, 'f')}" if at else ""
    if item and verb:
        amount = f"{num(abs(qty))} × " if qty is not None else ""
        name = discord.utils.escape_markdown(who) if who else "Someone"
        value = entry.get("value")
        worth = f" ({compact(value)})" if isinstance(value, (int, float)) and value and entry.get("item_key") != "coins" else ""
        return f"{emoji} **{name}** {verb} {amount}{item}{worth}{stamp}"
    # Unrecognised shape: show the fields as they are.
    shown = ", ".join(f"{title(k)}: {_display(k, v)}" for k, v in _flatten(entry)[:6] if k not in TIME_KEYS)
    return f"{emoji} {shown}{stamp}"


def request_line(req: dict[str, Any]) -> str:
    who = _name(_first(req, WHO_KEYS)) or "Someone"
    item, qty = item_text(req), qty_value(req)
    if item:
        amount = f"{num(abs(qty))} × " if qty is not None else ""
        return f"🙋 **{discord.utils.escape_markdown(who)}** asked for {amount}{item}"
    shown = ", ".join(f"{title(k)}: {_display(k, v)}" for k, v in _flatten(req)[:6])
    return f"🙋 {shown}"


def bank_image(data: dict[str, Any]) -> bytes:
    def stat(value: Any) -> str:
        return num(value) if isinstance(value, (int, float)) and value < 100_000 else compact(value)

    augments, cap = data.get("augment_count"), data.get("augment_vault_cap")
    queue, requests = data.get("enchant_queue"), data.get("requests")
    stats = [
        ("🪙", "Coins", stat(data.get("coins"))),
        ("💰", "Item value", stat(data.get("items_value"))),
        ("📦", "Items", stat(data.get("item_count"))),
        ("✨", "Augment vault", f"{num(augments)}" + (f" / {num(cap)}" if cap else "")),
        ("🪄", "Enchant queue", str(len(queue)) if isinstance(queue, list) else "?"),
        ("🙋", "Requests", str(len(requests)) if isinstance(requests, list) else "?"),
    ]
    items = [i for i in data.get("items") or [] if isinstance(i, dict)]

    def worth(i: dict[str, Any]) -> float:
        v = _first(i, VALUE_KEYS)
        return v if isinstance(v, (int, float)) else 0

    top = sorted(items, key=lambda i: (-worth(i), -(qty_value(i) or 0)))[:10]
    rows = [
        BankItem(item_text(i) or "?", num(qty_value(i)) if qty_value(i) is not None else "—",
                 stat(worth(i)) if worth(i) else "—", str(i.get("rarity") or ""))
        for i in top
    ]
    tabs = [t for t in data.get("tabs") or [] if isinstance(t, dict)]
    footer = f"{len(tabs)} tab{'s' if len(tabs) != 1 else ''}" if tabs else ""
    generated = timestamp(data.get("generated_at"))
    if generated:
        footer += (" · " if footer else "") + f"Updated {generated:%d %b %Y %H:%M} UTC · every 10 minutes"
    return render_bank("Clan bank", clan_name(data), stats, rows, footer)


def activity_embed(data: dict[str, Any]) -> discord.Embed:
    entries = [e for e in data.get("log") or [] if isinstance(e, dict)][: int(BANK_LOG_LIMIT)]
    if data.get("log_order", "newest_first") != "newest_first":
        entries.reverse()
    embed = discord.Embed(
        title="📜 Recent bank activity",
        description="\n".join(log_line(e) for e in entries) or "Nothing deposited or withdrawn yet.",
        colour=BANK_COLOUR,
    )
    requests = [r for r in data.get("requests") or [] if isinstance(r, dict)]
    if requests:
        embed.add_field(name="Withdrawal requests", value="\n".join(request_line(r) for r in requests)[:1024], inline=False)
    embed.set_footer(text=f"Last {int(BANK_LOG_LIMIT)} changes, newest first · updates every 10 minutes")
    return embed


def entry_kind(entry: dict[str, Any]) -> str:
    kind = str(_first(entry, KIND_KEYS) or "").lower()
    qty = qty_value(entry)
    if kind in IN_WORDS or "deposit" in kind:
        return "in"
    if kind in OUT_WORDS or "withdr" in kind or (qty is not None and qty < 0):
        return "out"
    return kind


def to_activity(entry: dict[str, Any], kind: str | None = None) -> Activity:
    qty, item = qty_value(entry), item_text(entry) or "?"
    value = entry.get("value")
    at = when(_first(entry, TIME_KEYS))
    return Activity(
        kind or entry_kind(entry),
        _name(_first(entry, WHO_KEYS)) or "?",
        f"{num(abs(qty))} × {item}" if qty is not None else item,
        compact(value) if isinstance(value, (int, float)) and value and entry.get("item_key") != "coins" else "—",
        f"{at:%d %b %H:%M}" if at else "",
    )


def activity_image(data: dict[str, Any]) -> bytes:
    entries = [e for e in data.get("log") or [] if isinstance(e, dict)][: int(BANK_LOG_LIMIT)]
    if data.get("log_order", "newest_first") != "newest_first":
        entries.reverse()
    requests = [r for r in data.get("requests") or [] if isinstance(r, dict)]
    rows = ([to_activity(r, "request") for r in requests] + [to_activity(e) for e in entries])[:MAX_ACTIVITY_ROWS]
    changes = sum(1 for r in rows if r.kind != "request")
    footer = f"Last {changes} change{'s' if changes != 1 else ''}, newest first"
    if requests:
        footer = f"{len(requests)} pending request{'s' if len(requests) != 1 else ''} at the top · " + footer
    return render_bank_activity("Recent bank activity", clan_name(data), rows, footer)


def clan_name(data: dict[str, Any]) -> str:
    clan = data.get("clan") or {}
    name, tag = clan.get("name") or "Clan", clan.get("tag")
    return f"{name} [{tag}]" if tag else str(name)


def clan_id(data: dict[str, Any]) -> Any:
    return (data.get("clan") or {}).get("id")


def bonus_text(node: dict[str, Any], value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "?"
    if node.get("bonus_type") == "clan_firepit_stage":
        return f"stage {value:g}"
    per = node.get("bonus_per_level")
    if isinstance(per, (int, float)) and 0 < per < 1:
        return f"+{value * 100:g}%"
    return f"+{value:g}"


def duration(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return "?"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days}d {hours}h" if days else f"{hours}h {rest // 60}m" if hours else f"{rest // 60}m"


# ---------- embeds ----------

def bank_embed(data: dict[str, Any]) -> discord.Embed:
    augments, cap = data.get("augment_count"), data.get("augment_vault_cap")
    lines = [
        f"🪙 **{num(data.get('coins'))}** coins",
        f"📦 **{num(data.get('item_count'))}** items worth **{compact(data.get('items_value'))}**",
        f"✨ Augment vault: **{num(augments)}**" + (f" / {num(cap)}" if cap else ""),
    ]
    if isinstance(queue := data.get("enchant_queue"), list):
        lines.append(f"🪄 Enchant queue: **{len(queue)}**")
    if isinstance(requests := data.get("requests"), list):
        lines.append(f"🙋 Withdrawal requests: **{len(requests)}**")
    embed = discord.Embed(
        title=f"🏦 {clan_name(data)} bank",
        description="\n".join(lines),
        colour=BANK_COLOUR,
        timestamp=timestamp(data.get("generated_at")),
    )

    items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    if items:
        def worth(i: dict[str, Any]) -> float:
            v = _first(i, VALUE_KEYS)
            return v if isinstance(v, (int, float)) else 0

        top = sorted(items, key=lambda i: (-worth(i), -(qty_value(i) or 0)))[:10]
        rows = []
        for i in top:
            qty, value = qty_value(i), _first(i, VALUE_KEYS)
            rows.append(
                f"{INDENT}{item_text(i) or '?'}" + (f" × {num(qty)}" if qty is not None else "")
                + (f" · {compact(value)}" if isinstance(value, (int, float)) else "")
            )
        embed.add_field(name="Top items", value="\n".join(rows)[:1024], inline=False)

    tabs = [t for t in data.get("tabs") or [] if isinstance(t, dict)]
    if len(tabs) > 1 or any(t.get("daily_limit") for t in tabs):
        embed.add_field(
            name="Tabs",
            value="\n".join(
                f"{INDENT}{t.get('name', '?')}"
                + (f" · {num(t['daily_limit'])}/day limit" if t.get("daily_limit") else "")
                + ("" if t.get("deposit_allowed", True) else " · no deposits")
                for t in tabs
            )[:1024],
            inline=False,
        )
    embed.set_footer(text="Updates every 10 minutes")
    return embed


PART_EMOJI = {"head": "👹", "body": "🛡️", "arms": "💪", "legs": "🦵"}


def _hp(value: Any, maximum: Any) -> str | None:
    if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
        return f"{bar(value / maximum, 10)} {compact(value)} / {compact(maximum)}"
    return None


def boss_alive(boss: dict[str, Any]) -> bool:
    return str(boss.get("status") or "alive").lower() == "alive"


def boss_key(boss: dict[str, Any]) -> str:
    return f"{boss.get('tier')}:{boss.get('started_at') or event_key(boss)}"


def boss_embed(boss: dict[str, Any], data: dict[str, Any]) -> discord.Embed:
    """Only text that changes with the fight (HP, shields, tier), so the message isn't edited every minute.

    Shape: {"tier", "max_tier_unlocked", "status": "alive", "started_at",
    "hp": {"current", "max", "pct"}, "parts": {"head": {"hp", "max_hp", "shield", "shield_max"}, ...},
    "window": {"attempts_per_member", "hours", "next_reset_at", "number", "seconds_until_reset", "started_at"}}
    Once killed: "status": "respawning", "defeated_at", "next_spawn_at" (12 hours later),
    "seconds_until_spawn", and no "window".
    """
    tier, alive = boss.get("tier"), boss_alive(boss)
    hp = boss.get("hp") if isinstance(boss.get("hp"), dict) else {}
    lines = []
    if text := _hp(hp.get("current"), hp.get("max")):
        lines.append(f"❤️ {text}" + (f" ({hp['pct']:g}%)" if isinstance(hp.get("pct"), (int, float)) else ""))
    if not alive:
        lines.append(f"💀 Status: **{title(boss.get('status'))}**")
    window = boss.get("window") if isinstance(boss.get("window"), dict) else {}
    if reset := when(window.get("next_reset_at")):
        per = window.get("attempts_per_member")
        lines.append(
            f"🔄 Attempts reset {discord.utils.format_dt(reset, 'R')}"
            + (f" · {per} each every {window.get('hours', '?')}h" if per else "")
        )
    if started := when(boss.get("started_at")):
        lines.append(f"⏱️ Up since {discord.utils.format_dt(started, 'R')}")
    embed = discord.Embed(
        title=f"⚔️ Clan Boss · Tier {tier}" if tier is not None else "⚔️ Clan Boss",
        description="\n".join(lines) or None,
        colour=BOSS_COLOUR if alive else discord.Colour.dark_grey(),
    )
    parts = boss.get("parts") if isinstance(boss.get("parts"), dict) else {}
    rows = []
    for name, part in parts.items():
        if not isinstance(part, dict):
            continue
        row = f"{PART_EMOJI.get(name, '•')} **{title(name)}** {_hp(part.get('hp'), part.get('max_hp')) or '?'}"
        if part.get("shield_max"):
            row += f" · 🛡️ {compact(part.get('shield'))} / {compact(part['shield_max'])}"
        rows.append(row)
    if rows:
        embed.add_field(name="Parts", value="\n".join(rows)[:1024], inline=False)
    unlocked = boss.get("max_tier_unlocked")
    embed.set_footer(text=f"{clan_name(data)}" + (f" · highest tier unlocked: {unlocked}" if unlocked else ""))
    return embed


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def boss_image(boss: dict[str, Any], data: dict[str, Any]) -> bytes:
    hp = boss.get("hp") if isinstance(boss.get("hp"), dict) else {}
    parts = boss.get("parts") if isinstance(boss.get("parts"), dict) else {}
    window = boss.get("window") if isinstance(boss.get("window"), dict) else {}
    footer = []
    if reset := when(window.get("next_reset_at")):
        per = window.get("attempts_per_member")
        footer.append(
            f"Attempts reset at {reset:%H:%M} UTC"
            + (f" · {per} per member every {window.get('hours', '?')}h" if per else "")
        )
    if not boss_alive(boss):
        down = [f"Defeated {d:%d %b %H:%M} UTC"] if (d := when(boss.get("defeated_at"))) else []
        if spawn := next_spawn(boss):
            down.append(f"next boss {spawn:%d %b %H:%M} UTC")
        if down:
            footer.append(" · ".join(down))
    elif started := when(boss.get("started_at")):
        footer.append(f"Up since {started:%d %b %H:%M} UTC")
    card = BossCard(
        subtitle=clan_name(data),
        tier=str(boss.get("tier", "?")),
        max_tier=str(boss.get("max_tier_unlocked", "?")),
        alive=boss_alive(boss),
        status=str(boss.get("status") or "alive"),
        hp=_number(hp.get("current")),
        max_hp=_number(hp.get("max")),
        parts=[
            BossPart(PART_EMOJI.get(name, "⚔️"), title(name), _number(p.get("hp")), _number(p.get("max_hp")),
                     _number(p.get("shield")), _number(p.get("shield_max")))
            for name, p in parts.items() if isinstance(p, dict)
        ],
        footer=footer,
    )
    return render_boss(card)


RESPAWN_KEYS = ("respawn_at", "next_spawn_at", "spawns_at", "next_boss_at", "cooldown_until", "cooldown_ends_at",
                "available_at", "next_at")


def next_spawn(boss: dict[str, Any]) -> datetime | None:
    """When the next boss spawns (a dead boss has "next_spawn_at", 12 hours after "defeated_at")."""
    nxt = _first(boss, RESPAWN_KEYS)
    if isinstance(nxt, dict):
        nxt = _first(nxt, RESPAWN_KEYS + ("at", "time"))
    return when(nxt)


def boss_text(boss: dict[str, Any]) -> str | None:
    """Message text above the card: Discord counts this down live (in each viewer's timezone), which an
    image can't."""
    if not boss_alive(boss):
        text = f"💀 **Clan boss tier {boss.get('tier', '?')} down**"
        if spawn := next_spawn(boss):
            text += (f" · next boss {discord.utils.format_dt(spawn, 'R')}"
                     f" ({discord.utils.format_dt(spawn, 't')})")
        return text
    window = boss.get("window") if isinstance(boss.get("window"), dict) else {}
    if reset := when(window.get("next_reset_at")):
        return f"🔄 Attempts reset {discord.utils.format_dt(reset, 'R')}"
    return None


BLUE_RGB, PURPLE_RGB, GREEN_RGB = (96, 165, 250), (168, 85, 247), (34, 197, 94)


def clan_level_card(data: dict[str, Any], old: int, level: int) -> bytes:
    xp = data.get("xp") or {}
    levels = {lv.get("level"): lv for lv in data.get("levels") or [] if isinstance(lv, dict)}
    members = (levels.get(level) or {}).get("max_members")
    before = (levels.get(old) or {}).get("max_members")
    count = (data.get("clan") or {}).get("member_count")
    pct = xp.get("progress_pct")
    stats = [
        ("👥", "Member slots", f"{num(members)}" + (f"  (+{members - before})" if isinstance(members, int)
                                                   and isinstance(before, int) and members > before else "")),
        ("📈", "Total clan XP", compact(xp.get("xp_total"))),
        ("⭐", "XP to next level", compact(xp.get("xp_to_next")) if xp.get("xp_to_next") else "Max level"),
    ]
    progress = None
    if isinstance(pct, (int, float)) and xp.get("xp_to_next"):
        progress = (pct / 100, f"Progress to level {level + 1}", f"{pct:g}%")
    return render_announcement(Announcement(
        "🏰", BLUE_RGB, "Clan level up!", clan_name(data),
        f"Level {old}  →  {level}",
        f"{num(count)} member{'s' if count != 1 else ''}" + (f" of {num(members)} slots" if members else "")
        + (f" · max level {xp['max_level']}" if xp.get("max_level") else ""),
        stats, progress,
        f"{datetime.now(timezone.utc):%d %b %Y %H:%M} UTC",
    ))


def materials_text(nxt: Any) -> str:
    if not isinstance(nxt, dict):
        return ""
    return ", ".join(
        f"{compact(m.get('qty'))} {m.get('item_name') or title(m.get('item_key'))}"
        for m in nxt.get("materials") or [] if isinstance(m, dict)
    )


def research_done_card(data: dict[str, Any], node: dict[str, Any], level: int) -> bytes:
    top = node.get("max_level")
    nxt = node.get("next")
    stats = [
        ("✨", "Bonus now", bonus_text(node, node.get("bonus_now"))),
        ("🎯", "Level", f"{level} / {top}" if top else str(level)),
        ("⏱️", "Next level takes", duration(nxt.get("time_sec")) if isinstance(nxt, dict) else "Maxed"),
    ]
    progress = (level / top, "Research progress", f"{level} / {top}") if isinstance(top, int) and top else None
    footer = f"Next: {materials_text(nxt)}" if isinstance(nxt, dict) else "Fully researched"
    return render_announcement(Announcement(
        "🏛️", PURPLE_RGB, "Research complete", f"{clan_name(data)} Institute",
        f"{node.get('name') or title(node.get('node_key'))} {level}",
        str(node.get("description") or ""),
        stats, progress, footer,
    ))


def research_started_card(data: dict[str, Any], name: str, node: dict[str, Any] | None, end: datetime | None) -> bytes:
    nxt = (node or {}).get("next")
    stats = [
        ("🎯", "Target level", str(nxt.get("target_level", "?")) if isinstance(nxt, dict) else "?"),
        ("⏱️", "Research time", duration(nxt.get("time_sec")) if isinstance(nxt, dict) else "?"),
        ("🏁", "Done at", f"{end:%d %b %H:%M} UTC" if end else "?"),
    ]
    if isinstance(speed := data.get("speed_bonus_pct"), (int, float)) and speed:
        stats.append(("⚡", "Research speed", f"+{speed:g}%"))
    return render_announcement(Announcement(
        "🧪", PURPLE_RGB, "Research started", f"{clan_name(data)} Institute",
        name, str((node or {}).get("description") or ""),
        stats, None,
        f"Materials: {materials_text(nxt)}" if isinstance(nxt, dict) else "",
    ))


def active_research(data: dict[str, Any]) -> tuple[str, str] | None:
    """(key, display name) of the research in progress, if any."""
    active = data.get("active")
    if not active:
        return None
    nodes = {n.get("node_key"): n for n in data.get("nodes") or [] if isinstance(n, dict)}
    if isinstance(active, dict):
        key = _first(active, ("node_key", "key", "node", "id"))
        node = nodes.get(key) if isinstance(key, str) else None
        name = (node or {}).get("name") or _name(_first(active, NAME_KEYS)) or title(key)
        target = _first(active, ("target_level", "level"))
        return f"{key}:{target}", f"{name}" + (f" level {target}" if target else "")
    return str(active), str(nodes.get(active, {}).get("name") or title(active))


def institute_embed(data: dict[str, Any]) -> discord.Embed:
    research = active_research(data)
    lines = [f"🧪 Researching: **{research[1]}**" if research else "🧪 Nothing being researched right now."]
    active = data.get("active")
    if isinstance(active, dict) and (end := when(_first(active, END_KEYS))):
        lines[0] += f" · done {discord.utils.format_dt(end, 'R')}"
    if isinstance(data.get("paused"), list) and data["paused"]:
        lines.append(f"⏸️ Paused: {len(data['paused'])}")
    if isinstance(speed := data.get("speed_bonus_pct"), (int, float)) and speed:
        lines.append(f"⚡ Research speed +{speed:g}%")
    embed = discord.Embed(
        title=f"🏛️ {clan_name(data)} Institute",
        description="\n".join(lines),
        colour=INSTITUTE_COLOUR,
        timestamp=timestamp(data.get("generated_at")),
    )
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        level, top = node.get("level", 0), node.get("max_level", "?")
        value = f"Level **{level}** / {top} · {bonus_text(node, node.get('bonus_now'))}"
        nxt = node.get("next")
        if isinstance(nxt, dict):
            mats = ", ".join(
                f"{compact(m.get('qty'))} {m.get('item_name') or title(m.get('item_key'))}"
                for m in nxt.get("materials") or [] if isinstance(m, dict)
            )
            value += f"\nNext: {mats} · {duration(nxt.get('time_sec'))}"
        embed.add_field(name=str(node.get("name") or title(node.get("node_key"))), value=value[:1024], inline=False)
    top = [t for t in data.get("lifetime_top") or [] if isinstance(t, dict)][:5]
    if top:
        embed.add_field(
            name="Top contributors (lifetime)",
            value="\n".join(
                f"{INDENT}{_name(_first(t, WHO_KEYS + NAME_KEYS)) or '?'}"
                + (f" · {compact(v)}" if isinstance(v := _first(t, VALUE_KEYS + ("contributed", "total", "xp")), (int, float)) else "")
                for t in top
            )[:1024],
            inline=False,
        )
    return embed


def clan_embed(data: dict[str, Any]) -> discord.Embed:
    xp = data.get("xp") or {}
    level, top = xp.get("level"), xp.get("max_level")
    pct = xp.get("progress_pct")
    lines = [f"Level **{num(level)}**" + (f" / {top}" if top else "")]
    if isinstance(pct, (int, float)) and xp.get("xp_to_next"):
        lines.append(f"{bar(pct / 100)} {pct:g}% · {num(xp.get('xp_to_next'))} XP to level {num((level or 0) + 1)}")
    members = [m for m in data.get("members") or [] if isinstance(m, dict)]
    week = sum(m.get("xp_this_week") or 0 for m in members)
    lines.append(f"👥 {num((data.get('clan') or {}).get('member_count'))} members · ⭐ {compact(week)} clan XP this week")
    embed = discord.Embed(
        title=f"🏰 {clan_name(data)}",
        description="\n".join(lines),
        colour=CLAN_COLOUR,
        timestamp=timestamp(data.get("generated_at")),
    )
    ranked = sorted(members, key=lambda m: -(m.get("xp_this_week") or 0))[:10]
    medals = ["🥇", "🥈", "🥉"]
    if ranked:
        embed.add_field(
            name="This week's contributors",
            value="\n".join(
                f"{medals[i] if i < 3 else f'`{i + 1}.`'} **{discord.utils.escape_markdown(str(m.get('character') or '?'))}**"
                f" · {compact(m.get('xp_this_week') or 0)}"
                + (f" · last week {compact(m['xp_last_week'])}" if m.get("xp_last_week") else "")
                for i, m in enumerate(ranked)
            )[:1024],
            inline=False,
        )
    if note := xp.get("share_note"):
        embed.set_footer(text=str(note)[:200])
    return embed


# ---------- /link-clan modal ----------

class ClanTokenModal(discord.ui.Modal, title="Set the clan API token"):
    token = discord.ui.TextInput(
        label="Clan API token",
        placeholder="The clan's token (64 characters), not a personal kxu_ one",
        min_length=10,
        max_length=200,
    )

    def __init__(self, cog: Clan):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.finish_link_clan(interaction, self.token.value.strip())

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("Clan token modal failed for user %s", interaction.user.id, exc_info=error)
        message = "Something went wrong while setting the clan token. The error has been logged."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


# ---------- per-boss damage ----------

MEMBER_PARAMS = {"weeks": "1", "detail": "members"}  # shared by the XP poll and the damage tracker
DAMAGE_AFTER_KILL = timedelta(minutes=10)  # keep updating this long after the kill, then freeze


def member_damage(data: dict[str, Any]) -> tuple[str | None, dict[str, int], dict[str, str]]:
    """(current week, {character_id: boss damage this week}, {character_id: name}) from clan-xp detail=members."""
    week = data.get("week_start_current")
    damage, names = {}, {}
    for m in data.get("weekly_members") or []:
        if not isinstance(m, dict) or m.get("week_start") != week or m.get("character_id") is None:
            continue
        cid = str(m["character_id"])
        damage[cid] = int(m.get("boss_damage") or 0)
        names[cid] = str(m.get("character") or "?")
    return week, damage, names


def damage_rows(dmg: dict[str, Any]) -> list[DamageRow]:
    """Damage on this boss per member: earlier weeks' share (if the boss spanned a weekly reset) plus
    this week's total minus the snapshot."""
    baseline, latest, carried = dmg.get("baseline") or {}, dmg.get("latest") or {}, dmg.get("carried") or {}
    done = {cid: carried.get(cid, 0) + max(latest.get(cid, 0) - baseline.get(cid, 0), 0)
            for cid in set(latest) | set(carried)}
    total = sum(done.values())
    names = dmg.get("names") or {}
    rows = [DamageRow(names.get(cid, "?"), v, v / total) for cid, v in done.items() if v > 0]
    return sorted(rows, key=lambda r: -r.damage)


def damage_image(dmg: dict[str, Any] | None, boss: dict[str, Any], data: dict[str, Any]) -> bytes:
    alive = boss_alive(boss)
    subtitle = f"Tier {boss.get('tier', '?')} · {clan_name(data)}"
    if not dmg or not dmg.get("baseline_at"):
        return render_boss_damage("Boss damage", subtitle, [],
                                  "Not tracked: the bot wasn't watching when this boss was fought", alive)
    rows = damage_rows(dmg)
    footer = []
    since, started = when(dmg.get("baseline_at")), when(boss.get("started_at"))
    if since and started and since - started > timedelta(minutes=3):
        footer.append(f"Counting since {since:%H:%M} UTC")
    if rows:
        count = f"Top {MAX_DAMAGE_ROWS} of {len(rows)} members" if len(rows) > MAX_DAMAGE_ROWS else \
            f"{len(rows)} member{'s' if len(rows) != 1 else ''}"
        footer.append(f"{count} · {compact(sum(r.damage for r in rows))} total")
    if dmg.get("frozen"):
        footer.append("Final")
    elif updated := when(dmg.get("updated_at")):
        footer.append(f"Updated {updated:%H:%M} UTC · every 5 min")
    return render_boss_damage("Boss damage", subtitle, rows, " · ".join(footer), alive)


# ---------- one clan's tracker ----------

class ClanTracker:
    """Everything for one clan: its token, polling and posting. Every server that linked this clan shares
    one tracker, so a clan is polled once (on its own token's 120/hour) however many servers show it.
    Saved state is keyed by the clan's id, so two clans never touch each other's."""

    def __init__(self, bot: KoruxaBot, clan: dict[str, Any], token: str, guilds: set[int]):
        self.bot = bot
        self.clan = clan  # id, name, tag
        self.token = token
        self.guilds = guilds
        self.token_ok = True
        self.last_bank: str | None = None
        self.last_boss: str | None = None
        self.tick_n = 0

    @property
    def name(self) -> str:
        return f"{self.clan.get('name') or 'Clan'} [{self.clan.get('tag') or '?'}]"

    def key(self, name: str) -> str:
        """bot_state key for this clan's copy of `name` (e.g. clan_boss:81)."""
        return f"{name}:{self.clan.get('id')}"

    async def targets(self, feature: str) -> list[Any]:
        """This clan's servers that have a channel set for `feature`."""
        return [cfg for cfg in await self.bot.db.feature_targets(feature) if cfg.guild_id in self.guilds]

    async def post(self, feature: str, **kwargs: Any) -> dict[int, discord.Message]:
        """post_alert, to this clan's servers only."""
        sent: dict[int, discord.Message] = {}
        for cfg in await self.targets(feature):
            sent.update(await post_alert(self.bot, feature, guild_id=cfg.guild_id, **kwargs))
        return sent

    async def fetch(self, path: str, params: dict[str, str] | None = None, max_wait: float = 60) -> dict[str, Any] | None:
        try:
            data = await self.bot.api.get(self.token, path, params, max_wait=max_wait)
        except TokenRevoked:
            if self.token_ok:
                log.warning("%s: the clan token was rejected (revoked or rotated?). Set a new one with /link-clan.",
                            self.name)
            self.token_ok = False
            return None
        except KoruxaError as e:
            log.warning("%s: /%s failed: %s", self.name, path, e)
            return None
        if not self.token_ok:
            log.info("%s: the clan token works again", self.name)
        self.token_ok = True
        return data

    async def tick(self, n: int) -> None:
        self.tick_n = n
        jobs = [
            (True, self.poll_boss),
            (n % 10 == 0, self.poll_bank),
            (n % 5 == 0, self.poll_xp),
            (n % 10 == 5, self.poll_institute),
            (True, self.weekly_recap),
        ]
        for due, job in jobs:
            if not due:
                continue
            try:
                await job()
            except Exception:
                log.exception("%s: clan update %s failed", self.name, job.__name__)

    # ---------- bank ----------

    async def poll_bank(self) -> None:
        """Two cards per server, both edited in place: the bank summary, and below it the latest changes
        (with pending withdrawal requests on top)."""
        if not await self.targets("clanbank"):
            return
        data = await self.fetch("clan-bank", {"limit": BANK_LOG_LIMIT})
        if data is None:
            return
        if requests := [r for r in data.get("requests") or [] if isinstance(r, dict)]:
            log.debug("Clan bank requests raw=%s", json.dumps(requests[0])[:1000])
        fingerprint = _embed_fingerprint(bank_embed(data)) + _embed_fingerprint(activity_embed(data))
        if fingerprint == self.last_bank:
            return
        cards = [
            (await asyncio.to_thread(bank_image, data), "clan-bank.png", None),
            (await asyncio.to_thread(activity_image, data), "bank-activity.png", None),
        ]
        for cfg in await self.targets("clanbank"):
            await self.show_cards("clanbank", cfg, cards)
        self.last_bank = fingerprint

    async def show_cards(self, feature: str, cfg: Any, cards: list[tuple[bytes, str, str | None]],
                         repost: bool = False) -> None:
        """Keep a fixed set of image messages (png, filename, text) in the feature's channel, edited
        in place. If one is gone, the channel changed, or `repost`, they are all posted again in order."""
        channel = self.bot.get_channel(cfg.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        key = f"{feature}_messages"  # per server, so it doesn't need the clan id
        state = await self.bot.db.get_state(key) or {}
        ids = state.get(str(cfg.guild_id)) or []

        def file(png: bytes, name: str) -> discord.File:
            return discord.File(io.BytesIO(png), filename=name)

        if not repost and len(ids) == len(cards) and cfg.status_message_id == ids[0]:
            try:
                for message_id, (png, name, text) in zip(ids, cards):
                    await channel.get_partial_message(message_id).edit(
                        content=text, embed=None, attachments=[file(png, name)],
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                return
            except discord.NotFound:
                pass  # one was deleted: start over below
            except discord.HTTPException as e:
                log.warning("Couldn't edit the %s cards in guild %s: %s", feature, cfg.guild_id, e)
                return
        for message_id in {*ids, cfg.status_message_id} - {None}:
            try:
                await channel.get_partial_message(message_id).delete()
            except discord.HTTPException:
                pass  # already gone
        sent = []
        try:
            for png, name, text in cards:
                sent.append(await channel.send(content=text, file=file(png, name),
                                               allowed_mentions=discord.AllowedMentions.none()))
        except discord.HTTPException as e:
            log.warning("Couldn't post the %s cards in guild %s: %s", feature, cfg.guild_id, e)
        if sent:
            await self.bot.db.set_feature_field(cfg.guild_id, feature, "status_message_id", sent[0].id)
        state = await self.bot.db.get_state(key) or {}  # re-read: another clan may have saved meanwhile
        state[str(cfg.guild_id)] = [m.id for m in sent]
        await self.bot.db.set_state(key, state)

    # ---------- boss ----------

    async def poll_boss(self) -> None:
        """Two cards (the boss, and the damage on it), edited when anything on them changes. While the boss is
        alive a Clan Boss role ping sits under them; it is deleted once the boss is dead, and a new one is
        posted when the next boss is up, or when the 4-hourly attempts refresh while it's still alive."""
        if not await self.targets("clanboss"):
            return
        data = await self.fetch("clan-boss")
        if data is None:
            return
        boss = data.get("boss")
        state = await self.bot.db.get_state(self.key("clan_boss")) or {}

        if not isinstance(boss, dict) or not boss:
            if state.get("key"):
                log.info("%s: clan boss %s gone", self.name, state["key"])
            await self.delete_pings(state)
            await self.bot.db.set_state(self.key("clan_boss"), {"pings": {}})
            return

        key, alive = boss_key(boss), boss_alive(boss)
        if (key, alive) != (state.get("key"), state.get("alive")):
            log.info("%s: clan boss %s alive=%s raw=%s", self.name, key, alive, json.dumps(boss)[:4000])

        dmg = await self.track_damage(boss, key, alive)

        # The embed text covers everything on the boss card, so it doubles as the change check.
        fingerprint = _embed_fingerprint(boss_embed(boss, data)) + json.dumps(
            [(r.player, r.damage) for r in damage_rows(dmg or {})] + [bool((dmg or {}).get("frozen"))])
        if fingerprint != self.last_boss:
            png = await asyncio.to_thread(boss_image, boss, data)
            damage_png = await asyncio.to_thread(damage_image, dmg, boss, data)
            for cfg in await self.targets("clanboss"):
                card_before = (await self.bot.db.get_state("clanboss_messages") or {}).get(str(cfg.guild_id))
                await self.show_cards("clanboss", cfg, [(png, "clan-boss.png", boss_text(boss)),
                                                        (damage_png, "boss-damage.png", None)])
                card_after = (await self.bot.db.get_state("clanboss_messages") or {}).get(str(cfg.guild_id))
                if card_after != card_before:
                    await self.delete_pings(state, cfg.guild_id)  # the card was reposted: the ping goes below it
            self.last_boss = fingerprint

        pings = state.setdefault("pings", {})
        window = boss.get("window") if isinstance(boss.get("window"), dict) else {}
        number = window.get("number")
        # Attempts came back while the same boss is still up: swap the ping for a fresh one.
        refreshed = (alive and key == state.get("key") and number is not None
                     and state.get("window") is not None and number != state.get("window"))
        if not alive or key != state.get("key") or refreshed:
            await self.delete_pings(state)
        if refreshed:
            log.info("%s: clan boss %s attempts refreshed (window %s)", self.name, key, number)
        if alive:
            tier = boss.get("tier", "?")
            if refreshed:
                per = window.get("attempts_per_member")
                content = (f"🔄 **Clan boss attempts refreshed!** "
                           f"{f'{per} more' if per else 'More'} attempts on tier {tier}. Go hit it.")
            else:
                content = f"⚔️ **Clan boss tier {tier} is alive!** Go hit it."
            for cfg in await self.targets("clanboss"):
                if str(cfg.guild_id) in pings:
                    continue
                sent = await post_alert(self.bot, "clanboss", guild_id=cfg.guild_id, content=content)
                if message := sent.get(cfg.guild_id):
                    pings[str(cfg.guild_id)] = message.id
        state.update({"key": key, "alive": alive, "tier": boss.get("tier"), "window": number})
        await self.bot.db.set_state(self.key("clan_boss"), state)

    async def track_damage(self, boss: dict[str, Any], key: str, alive: bool) -> dict[str, Any] | None:
        """Per-member damage on this boss, from weekly totals minus a snapshot taken when the bot first saw
        it alive. Fetches clan-xp only at the spawn, every 5 minutes (with the XP poll's request), on the
        kill, and for DAMAGE_AFTER_KILL after it; then the result is frozen."""
        dmg = await self.bot.db.get_state(self.key("clan_boss_damage")) or {}
        if dmg.get("boss") != key:
            dmg = {"boss": key}
            if not alive:
                await self.bot.db.set_state(self.key("clan_boss_damage"), dmg)  # first seen dead: nothing to measure
                return dmg
        if dmg.get("frozen") or (not dmg.get("baseline_at") and not alive):
            return dmg

        defeated = when(boss.get("defeated_at"))
        just_died = not alive and dmg.get("alive", True)
        due = (not dmg.get("baseline_at") or just_died or self.tick_n % 5 == 0)
        if not due:
            return dmg
        data = await self.fetch("clan-xp", MEMBER_PARAMS)
        if data is None:
            return dmg
        week, damage, names = member_damage(data)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if not dmg.get("baseline_at"):
            dmg.update({"week": week, "baseline": damage, "latest": damage, "carried": {}, "names": names,
                        "baseline_at": now})
            log.info("%s: boss damage %s snapshot of %d members (total %s)", self.name, key, len(damage),
                     sum(damage.values()))
        else:
            if week != dmg.get("week"):
                # A weekly reset mid-fight: bank what was done before it; the new week starts from 0.
                carried = dmg.get("carried") or {}
                for cid, value in (dmg.get("latest") or {}).items():
                    done = value - (dmg.get("baseline") or {}).get(cid, 0)
                    if done > 0:
                        carried[cid] = carried.get(cid, 0) + done
                dmg.update({"week": week, "baseline": {}, "carried": carried})
                log.info("%s: boss damage %s, weekly reset during the fight", self.name, key)
            before = sum(r.damage for r in damage_rows(dmg))
            dmg["latest"] = damage
            dmg["names"] = {**(dmg.get("names") or {}), **names}
            after = sum(r.damage for r in damage_rows(dmg))
            if after != before:
                log.info("%s: boss damage %s, %s total (+%s since last check)", self.name, key, after, after - before)
        dmg["updated_at"] = now
        dmg["alive"] = alive
        if not alive and defeated and datetime.now(timezone.utc) - defeated >= DAMAGE_AFTER_KILL:
            dmg["frozen"] = True
            log.info("%s: boss damage %s final, %s", self.name, key,
                     ", ".join(f"{r.player} {compact(r.damage)}" for r in damage_rows(dmg)[:5]))
        await self.bot.db.set_state(self.key("clan_boss_damage"), dmg)
        return dmg

    async def delete_pings(self, state: dict[str, Any], guild_id: int | None = None) -> None:
        """Remove the role ping under the boss card (in one server, or all of this clan's)."""
        pings = state.setdefault("pings", {})
        channels = {cfg.guild_id: cfg.channel_id for cfg in await self.bot.db.feature_targets("clanboss")}
        for guild, message_id in list(pings.items()):
            if guild_id is not None and guild != str(guild_id):
                continue
            channel = self.bot.get_channel(channels.get(int(guild), 0))
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.get_partial_message(message_id).delete()
                except discord.HTTPException:
                    pass  # already gone
            del pings[guild]

    # ---------- XP and Institute (Clan Feed) ----------

    async def poll_xp(self) -> None:
        if not await self.targets("feed"):
            return
        data = await self.fetch("clan-xp", MEMBER_PARAMS)  # same request as the damage tracker, so it's cached
        if data is None:
            return
        level = (data.get("xp") or {}).get("level")
        if not isinstance(level, int):
            return
        state = await self.bot.db.get_state(self.key("clan_xp")) or {}
        old = state.get("level")
        if isinstance(old, int) and level > old:
            log.info("%s levelled %s -> %s", self.name, old, level)
            # The default answer leaves out part of the level curve; member slots come from it.
            full = await self.fetch("clan-xp", {"curve": "1"}) or data
            png = await asyncio.to_thread(clan_level_card, full, old, level)
            await self.post("feed", image=png, filename="clan-level.png", ping=False)
        await self.bot.db.set_state(self.key("clan_xp"), {"level": level})

    async def poll_institute(self) -> None:
        if not await self.targets("feed"):
            return
        data = await self.fetch("clan-institute")
        if data is None:
            return
        nodes = {n["node_key"]: n for n in data.get("nodes") or [] if isinstance(n, dict) and n.get("node_key")}
        levels = {k: n.get("level", 0) for k, n in nodes.items()}
        research = active_research(data)
        state = await self.bot.db.get_state(self.key("clan_institute"))

        if state is not None:
            before = state.get("levels") or {}
            for key, level in levels.items():
                if isinstance(before.get(key), int) and level > before[key]:
                    node = nodes[key]
                    log.info("%s Institute: %s reached level %s", self.name, key, level)
                    png = await asyncio.to_thread(research_done_card, data, node, level)
                    await self.post("feed", image=png, filename="research-complete.png", ping=False)
            if research and research[0] != state.get("active"):
                log.info("%s Institute: research started, raw=%s", self.name, json.dumps(data.get("active"))[:1000])
                active = data.get("active")
                end = when(_first(active, END_KEYS)) if isinstance(active, dict) else None
                node = nodes.get(research[0].split(":")[0])
                png = await asyncio.to_thread(research_started_card, data, research[1], node, end)
                await self.post("feed", image=png, filename="research-started.png", ping=False)
        await self.bot.db.set_state(self.key("clan_institute"), {
            "levels": levels, "active": research[0] if research else None,
        })

    # ---------- weekly recap ----------

    def last_weekly_reset(self, now: datetime) -> datetime:
        cfg = self.bot.config
        reset = now.replace(hour=cfg.weekly_reset_hour, minute=0, second=0, microsecond=0)
        reset -= timedelta(days=(now.weekday() - cfg.weekly_reset_day) % 7)
        return reset if reset <= now else reset - timedelta(days=7)

    async def weekly_recap(self) -> None:
        """Post the clan's totals and top contributors for the week just ended, once, after the reset."""
        now = datetime.now(timezone.utc)
        reset = self.last_weekly_reset(now)
        state = await self.bot.db.get_state(self.key("clan_weekly")) or {}
        if state.get("reset") == reset.isoformat():
            return
        if now - reset < timedelta(minutes=2):
            return  # give the game a moment to close the week
        await self.bot.db.set_state(self.key("clan_weekly"), {"reset": reset.isoformat()})
        if not state or now - reset > timedelta(hours=6):
            log.info("%s weekly recap: skipping the %s reset (first start or too late)", self.name, reset.isoformat())
            return
        if not await self.targets("feed"):
            return
        data = await self.fetch("clan-xp", {"weeks": "2", "detail": "members"}, max_wait=300)
        if data is None:
            return
        png = await asyncio.to_thread(recap_image, data, reset)
        if png is None:
            log.info("%s weekly recap: no data for the week before %s", self.name, reset.date())
            return
        await self.post("feed", image=png, filename="clan-week.png", ping=False)
        log.info("Posted the weekly recap for %s", self.name)


def recap_image(data: dict[str, Any], reset: datetime) -> bytes | None:
    week_start = (reset - timedelta(days=7)).date()
    key = week_start.isoformat()
    week = next((w for w in data.get("weekly") or [] if isinstance(w, dict) and w.get("week_start") == key), None)
    if week is None:
        return None
    members = sorted(
        (m for m in data.get("weekly_members") or [] if isinstance(m, dict) and m.get("week_start") == key),
        key=lambda m: -(m.get("xp") or 0),
    )[:5]

    def stat(value: Any) -> str:
        return compact(value) if isinstance(value, (int, float)) and value else "—"

    def hours(value: Any) -> str:
        return f"{value:g}h" if isinstance(value, (int, float)) and value else "—"

    stats = [
        ("⭐", "Clan XP", stat(week.get("xp"))),
        ("📜", "Quests", stat(week.get("quests"))),
        ("⚔️", "Boss damage", stat(week.get("boss_damage"))),
        ("🏦", "Bank value", stat(week.get("bank_value"))),
        ("🔥", "Firepit time", hours(week.get("firepit_hours"))),
        ("👥", "Active members", stat(week.get("active_members"))),
    ]
    rows = [
        Contributor(str(m.get("character") or "?"), stat(m.get("xp")), stat(m.get("quests")),
                    stat(m.get("boss_damage")), hours(m.get("firepit_hours")))
        for m in members
    ]
    xp = data.get("xp") or {}
    footer = f"Clan level {num(xp.get('level'))}"
    if isinstance(pct := xp.get("progress_pct"), (int, float)):
        footer += f" · {pct:g}% to level {num((xp.get('level') or 0) + 1)}"
    week_end = reset.date() - timedelta(days=1)
    return render_clan_week(
        "Weekly clan recap",
        f"{clan_name(data)} · {week_start:%a %d %b} – {week_end:%a %d %b}",
        stats, rows, footer,
    )


# ---------- the cog ----------

# Per-clan tracker state; before multi-clan support these were single keys (migrated in cog_load).
TRACKER_STATE = ("clan_boss", "clan_boss_damage", "clan_xp", "clan_institute", "clan_weekly")


class Clan(commands.Cog):
    """Each Discord server links its own clan token (/link-clan), saved encrypted in bot_state "guild_clans"
    as {guild_id: {token_enc, clan: {id, name, tag}, set_by, set_at}}. Servers that link the same clan share
    one ClanTracker."""

    def __init__(self, bot: KoruxaBot):
        self.bot = bot
        self.links: dict[str, dict[str, Any]] = {}  # guild id -> saved link
        self.trackers: dict[Any, ClanTracker] = {}  # clan id -> tracker
        self._ticks = 0

    async def cog_load(self) -> None:
        await self.migrate_single_token()
        self.links = await self.bot.db.get_state("guild_clans") or {}
        self.rebuild()
        for tracker in self.trackers.values():
            log.info("Tracking %s for %d server(s)", tracker.name, len(tracker.guilds))
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    async def migrate_single_token(self) -> None:
        """Before multi-clan support there was one clan token for the whole bot. Give it to the server that
        uses the clan features (or GUILD_ID), and move its tracker state to that clan's keys."""
        old = await self.bot.db.get_state("clan_token") or {}
        if await self.bot.db.get_state("guild_clans") is not None:
            return
        links: dict[str, Any] = {}
        current = await self.bot.db.get_state("clan_current") or {}
        if old.get("token_enc") and current.get("id") is not None:
            guilds = {cfg.guild_id for f in ("clanboss", "clanbank", "feed") for cfg in await self.bot.db.feature_targets(f)}
            home = self.bot.config.guild_id or (next(iter(guilds)) if len(guilds) == 1 else None)
            if home is not None:
                links[str(home)] = {"token_enc": old["token_enc"], "set_by": old.get("set_by"), "set_at": old.get("set_at"),
                                    "clan": {k: current.get(k) for k in ("id", "name", "tag")}}
                for name in TRACKER_STATE:
                    if (value := await self.bot.db.get_state(name)) is not None:
                        await self.bot.db.set_state(f"{name}:{current['id']}", value)
                log.info("Moved the clan token to server %s (clan %s)", home, current.get("tag"))
            else:
                log.warning("Couldn't tell which server the old clan token belongs to; run /link-clan again")
        await self.bot.db.set_state("guild_clans", links)
        await self.bot.db.set_state("clan_token", {})

    def token_for(self, link: dict[str, Any]) -> str:
        try:
            return self.bot.cipher.decrypt(link["token_enc"].encode())
        except (TokenDecryptError, KeyError) as e:
            log.warning("Ignoring a saved clan token: %s", e)
            return ""

    def rebuild(self) -> None:
        """Group the servers' links by clan. Existing trackers are kept, so their change checks carry on."""
        groups: dict[Any, dict[str, Any]] = {}
        for gid, link in self.links.items():
            clan = link.get("clan") or {}
            token = self.token_for(link)
            if not token or clan.get("id") is None:
                continue
            group = groups.setdefault(clan["id"], {"clan": clan, "guilds": set(), "token": token, "at": ""})
            group["guilds"].add(int(gid))
            if str(link.get("set_at") or "") >= group["at"]:  # the most recently linked token wins
                group.update(token=token, at=str(link.get("set_at") or ""), clan=clan)
        for cid in list(self.trackers):
            if cid not in groups:
                self.bot.api.forget(self.trackers.pop(cid).token)
        for cid, group in groups.items():
            tracker = self.trackers.get(cid)
            if tracker is None:
                self.trackers[cid] = ClanTracker(self.bot, group["clan"], group["token"], group["guilds"])
                continue
            if tracker.token != group["token"]:
                self.bot.api.forget(tracker.token)
                tracker.token, tracker.token_ok = group["token"], True
            tracker.clan, tracker.guilds = group["clan"], group["guilds"]

    def tracker_for(self, guild_id: int | None) -> ClanTracker | None:
        link = self.links.get(str(guild_id)) if guild_id is not None else None
        return self.trackers.get((link.get("clan") or {}).get("id")) if link else None

    def clan_tag_for(self, guild_id: int | None) -> str:
        """The clan tag of the token this server linked ("" if none)."""
        link = self.links.get(str(guild_id)) if guild_id is not None else None
        return str(((link or {}).get("clan") or {}).get("tag") or "")

    @tasks.loop(minutes=1)
    async def tick(self) -> None:
        n, self._ticks = self._ticks, self._ticks + 1
        for tracker in list(self.trackers.values()):
            await tracker.tick(n)

    @tick.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ---------- commands ----------

    async def command_data(self, interaction: discord.Interaction, path: str, public: bool,
                           params: dict[str, str] | None = None) -> dict[str, Any] | None:
        tracker = self.tracker_for(interaction.guild_id)
        if tracker is None:
            await interaction.response.send_message(
                "This server hasn't linked a clan token, so I can't read clan data. An admin can add it with /link-clan.",
                ephemeral=True,
            )
            return None
        await interaction.response.defer(ephemeral=not public, thinking=True)
        data = await tracker.fetch(path, params, max_wait=10)
        if data is None:
            await interaction.followup.send(
                "Couldn't read the clan data right now." if tracker.token_ok
                else "The clan token was rejected. An admin needs to set a new one with /link-clan."
            )
        return data

    @app_commands.command(name="clan", description="Clan level and this week's top XP contributors")
    @app_commands.describe(public="Show it to everyone in the channel (default: only you)")
    @app_commands.guild_only()
    async def clan(self, interaction: discord.Interaction, public: bool = False) -> None:
        if data := await self.command_data(interaction, "clan-xp", public):
            await interaction.followup.send(embed=clan_embed(data))

    @app_commands.command(name="clanbank", description="What's in the clan bank")
    @app_commands.describe(public="Show it to everyone in the channel (default: only you)")
    @app_commands.guild_only()
    async def clanbank(self, interaction: discord.Interaction, public: bool = False) -> None:
        if data := await self.command_data(interaction, "clan-bank", public, {"limit": BANK_LOG_LIMIT}):
            await interaction.followup.send(embed=bank_embed(data))

    @app_commands.command(name="institute", description="Clan Institute research levels and what's next")
    @app_commands.describe(public="Show it to everyone in the channel (default: only you)")
    @app_commands.guild_only()
    async def institute(self, interaction: discord.Interaction, public: bool = False) -> None:
        if data := await self.command_data(interaction, "clan-institute", public):
            await interaction.followup.send(embed=institute_embed(data))

    # ---------- admin: this server's clan token ----------

    @app_commands.command(name="link-clan", description="Set this server's clan API token for the clan bank, boss, XP and Institute (admin)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def link_clan(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ClanTokenModal(self))

    async def finish_link_clan(self, interaction: discord.Interaction, token: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if any(c.isspace() for c in token):
            await interaction.followup.send("That doesn't look like a token: it contains spaces. Copy it again from the game.")
            return
        try:
            data = await self.bot.api.get(token, "clan-xp", fresh=True, max_wait=10)
        except TokenRevoked:
            await interaction.followup.send(
                "Koruxa rejected that token. It needs the **clan's** API token; a personal kxu_ token won't work here."
            )
            return
        except RateLimited as e:
            await interaction.followup.send(f"That token has hit its API rate limit. Try again in about {e.retry_after:.0f} s.")
            return
        except KoruxaError as e:
            log.warning("Clan token check failed: %s", e)
            await interaction.followup.send("Couldn't reach the Koruxa API to check the token. Try again shortly.")
            return
        clan = data.get("clan") if isinstance(data.get("clan"), dict) else {}
        if clan.get("id") is None:
            await interaction.followup.send("Koruxa didn't say which clan that token belongs to. Try again shortly.")
            return

        gid = str(interaction.guild_id)
        replaced = gid in self.links
        self.links[gid] = {
            "token_enc": self.bot.cipher.encrypt(token).decode(),
            "set_by": interaction.user.id,
            "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "clan": {k: clan.get(k) for k in ("id", "name", "tag")},
        }
        await self.bot.db.set_state("guild_clans", self.links)
        self.rebuild()
        log.info("User %s linked clan %s in guild %s", interaction.user.id, clan.get("id"), gid)

        tag = clan.get("tag")
        lines = [f"{'🔄 Replaced' if replaced else '✅ Set'} this server's clan token: **{clan_name(data)}**."]
        if tag:
            lines.append(f"This server's highscores, clan feed and /topgains now count **[{tag}]** members.")
        lines.append("Clan bank, boss, XP and Institute updates start within a minute. "
                     "The token is stored encrypted; **/clear-clan** removes it.")
        await interaction.followup.send("\n".join(lines))

    @app_commands.command(name="clear-clan", description="Remove this server's clan API token (admin)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def clear_clan(self, interaction: discord.Interaction) -> None:
        link = self.links.pop(str(interaction.guild_id), None)
        if link is None:
            await interaction.response.send_message("This server has no clan token set.", ephemeral=True)
            return
        await self.bot.db.set_state("guild_clans", self.links)
        self.rebuild()
        log.info("User %s cleared the clan token in guild %s", interaction.user.id, interaction.guild_id)
        clan = link.get("clan") or {}
        await interaction.response.send_message(
            f"🗑️ Removed this server's clan token for **{clan.get('name') or 'the clan'}**. Clan bank, boss, XP and "
            "Institute updates are off here, and the highscores show every linked character, until **/link-clan** "
            "is used again.",
            ephemeral=True,
        )


async def setup(bot: KoruxaBot) -> None:
    await bot.add_cog(Clan(bot))
