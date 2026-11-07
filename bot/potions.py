"""Weekly clan-potion usage, from the clan bank log.

Koruxa exposes no potion buff durations, so this tracks a simpler, real thing:
how many potions each member drew from the clan bank this week (withdrawals +
gifts), per potion type. Pure logic (no Discord); works off clan-bank `log`
entries, with a demo generator for when no clan is linked.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

CONSUMING_ACTIONS = {"withdraw", "give"}  # count against the taker
RETURNING_ACTIONS = {"deposit"}           # credit back (brew-and-bank)
# Koruxa potion item keys look like thieving_potion, super_defence_potion,
# hunters_draught, plus the odd elixir/brew/tonic. Match those shapes.
_POTION_KEY = re.compile(r"(_potion|_draught|_elixir|_tonic|_brew)$", re.I)


def is_potion(item_key: Any, name: Any = "") -> bool:
    key = str(item_key or "")
    if _POTION_KEY.search(key):
        return True
    low = str(name or "").strip().lower()
    return low.endswith(("potion", "draught", "elixir", "brew", "tonic"))


@dataclass
class MemberUsage:
    name: str
    total: int = 0
    by_potion: dict[str, int] = field(default_factory=dict)

    def top(self, n: int = 4) -> list[tuple[str, int]]:
        return sorted(self.by_potion.items(), key=lambda kv: -kv[1])[:n]


@dataclass
class UsageSummary:
    week_start: datetime
    members: list[MemberUsage]          # ranked, most potions first
    potion_totals: list[tuple[str, str, int]]  # (key, display name, qty), most first
    total: int

    @property
    def week_label(self) -> str:
        return self.week_start.strftime("%Y-%m-%d")

    def name_of(self, key: str) -> str:
        for k, name, _ in self.potion_totals:
            if k == key:
                return name
        return key


def week_start(moment: datetime | None = None) -> datetime:
    now = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=midnight.weekday())


def _parse_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def summarize_usage(
    log: list[dict[str, Any]],
    start: datetime,
    char_owner: dict[int, int] | None = None,
    char_name: dict[int, str] | None = None,
) -> UsageSummary:
    """Net potion usage per player for the week beginning `start`.

    Net = withdrawals + gifts − deposits, so brew-and-bank cancels out. Entries
    are grouped by the player who owns the character (via char_owner: character_id
    → owner id), so banking on one linked character and withdrawing on another
    nets to zero. Unlinked characters are grouped on their own.
    """
    char_owner = char_owner or {}
    char_name = char_name or {}
    end = start + timedelta(days=7)
    groups: dict[tuple[str, Any], dict[str, Any]] = {}
    display: dict[str, str] = {}

    for entry in log or []:
        action = str(entry.get("action", "")).lower()
        sign = 1 if action in CONSUMING_ACTIONS else (-1 if action in RETURNING_ACTIONS else 0)
        if sign == 0:
            continue
        key = str(entry.get("item_key", ""))
        if not is_potion(key, entry.get("name")):
            continue
        at = _parse_at(entry.get("at"))
        if at is None or at < start or at >= end:
            continue
        try:
            qty = int(entry.get("quantity"))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        name = str(entry.get("character", "")).strip()
        try:
            cid = int(entry.get("character_id"))
        except (TypeError, ValueError):
            cid = None
        owner = char_owner.get(cid) if cid is not None else None
        if owner is not None:
            gkey: tuple[str, Any] = ("user", owner)
        elif cid is not None:
            gkey = ("char", cid)
        else:
            gkey = ("name", name.casefold())

        group = groups.setdefault(gkey, {"by": {}, "labels": Counter()})
        group["by"][key] = group["by"].get(key, 0) + sign * qty
        label = (char_name.get(cid) if cid is not None else None) or name
        if label:
            group["labels"][label] += qty  # pick the most-active name as the group label
        display[key] = str(entry.get("name") or key)

    members: list[MemberUsage] = []
    for group in groups.values():
        label = group["labels"].most_common(1)[0][0] if group["labels"] else "Unknown"
        by = dict(group["by"])
        members.append(MemberUsage(label, sum(by.values()), by))
    members.sort(key=lambda m: (-m.total, m.name.casefold()))

    totals: dict[str, int] = {}
    for m in members:
        for k, q in m.by_potion.items():
            totals[k] = totals.get(k, 0) + q
    potion_totals = sorted(((k, display.get(k, k), q) for k, q in totals.items()), key=lambda t: -t[2])
    return UsageSummary(start, members, potion_totals, sum(totals.values()))


# --------------------------------------------------------------------------- #
# Demo data (when no clan is linked): real Koruxa potion names, fake members.  #
# --------------------------------------------------------------------------- #
_DEMO_POTIONS = [
    ("thieving_potion", "Thieving Potion"), ("super_thieving_potion", "Super Thieving Potion"),
    ("haste_potion", "Haste Potion"), ("wisdom_potion", "Wisdom Potion"),
    ("attack_potion", "Attack Potion"), ("health_potion", "Health Potion"),
    ("super_defence_potion", "Super Defence Potion"), ("hunters_draught", "Hunter's Draught"),
]
_DEMO_MEMBERS = ["Riftbreaker", "Mossgrove", "xX_Slayer_Xx", "Pudge", "TotoroHero",
                 "Mayhem", "Grindset", "Lil Nugget", "Quietstorm", "Emberfall"]


def demo_usage() -> UsageSummary:
    rng = random.Random(11)
    start = week_start()
    log: list[dict[str, Any]] = []
    tx = 1
    for member in _DEMO_MEMBERS:
        draws = rng.randint(2, 7)
        for _ in range(draws):
            key, name = rng.choice(_DEMO_POTIONS)
            qty = rng.randint(20, 1800)
            when = start + timedelta(seconds=rng.randint(0, 7 * 24 * 3600 - 1))
            log.append({"id": tx, "at": when.isoformat(), "action": "withdraw",
                        "item_key": key, "name": name, "quantity": qty, "character": member})
            tx += 1
    return summarize_usage(log, start)
