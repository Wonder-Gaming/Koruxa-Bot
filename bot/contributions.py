"""Clan bank contribution board, from the bank log.

Per player (grouped across their linked characters, like potions/loans): value
deposited vs withdrawn, netted. Items are valued at the current wiki market price
when cached, else the bank log's unit value — so contributions reflect real worth.
Pure logic (no Discord); a demo generator covers the no-clan case.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

DEPOSIT_ACTIONS = {"deposit"}
WITHDRAW_ACTIONS = {"withdraw", "give"}


@dataclass
class MemberContribution:
    name: str
    deposited: int = 0        # value in
    withdrawn: int = 0        # value out
    transactions: int = 0
    labels: Any = field(default=None)

    @property
    def net(self) -> int:
        return self.deposited - self.withdrawn


@dataclass
class ContributionSummary:
    members: list[MemberContribution]   # ranked by net, highest first
    total_deposited: int
    total_withdrawn: int

    @property
    def net(self) -> int:
        return self.total_deposited - self.total_withdrawn


def _parse_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def summarize_contributions(
    log: list[dict[str, Any]],
    price_map: dict[str, int] | None = None,
    char_owner: dict[int, int] | None = None,
    char_name: dict[int, str] | None = None,
    since: datetime | None = None,
) -> ContributionSummary:
    """Value in/out per player. price_map: item_key -> market unit price (coins);
    falls back to the entry's own unit_value when an item isn't priced."""
    from collections import Counter
    price_map = price_map or {}
    char_owner = char_owner or {}
    char_name = char_name or {}
    groups: dict[tuple[str, Any], MemberContribution] = {}

    for entry in log or []:
        action = str(entry.get("action", "")).lower()
        if action in DEPOSIT_ACTIONS:
            direction = "in"
        elif action in WITHDRAW_ACTIONS:
            direction = "out"
        else:
            continue
        at = _parse_at(entry.get("at"))
        if since is not None and (at is None or at < since):
            continue
        try:
            qty = int(entry.get("quantity"))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        key = str(entry.get("item_key", ""))
        unit = price_map.get(key)
        if unit is None:
            try:
                unit = int(entry.get("unit_value") or 0)
            except (TypeError, ValueError):
                unit = 0
        value = qty * max(0, int(unit))
        name = str(entry.get("character", "")).strip()
        try:
            cid = int(entry.get("character_id"))
        except (TypeError, ValueError):
            cid = None
        owner = char_owner.get(cid) if cid is not None else None
        gkey: tuple[str, Any] = ("user", owner) if owner is not None else (("char", cid) if cid is not None else ("name", name.casefold()))

        member = groups.get(gkey)
        if member is None:
            member = MemberContribution(name=name, labels=Counter())
            groups[gkey] = member
        label = (char_name.get(cid) if cid is not None else None) or name
        if label:
            member.labels[label] += qty
        member.transactions += 1
        if direction == "in":
            member.deposited += value
        else:
            member.withdrawn += value

    members = list(groups.values())
    for m in members:
        if m.labels:
            m.name = m.labels.most_common(1)[0][0]
    members.sort(key=lambda m: (-m.net, m.name.casefold()))
    return ContributionSummary(members,
                               sum(m.deposited for m in members),
                               sum(m.withdrawn for m in members))


# Demo board (no clan linked): fabricated members + values.
def demo_contributions() -> ContributionSummary:
    rng = random.Random(5)
    names = ["Riftbreaker", "Mossgrove", "xX_Slayer_Xx", "Pudge", "TotoroHero",
             "Mayhem", "Grindset", "Lil Nugget", "Quietstorm", "Emberfall"]
    log = []
    tx = 1
    for n in names:
        for _ in range(rng.randint(2, 6)):
            action = rng.choice(["deposit", "deposit", "withdraw"])
            log.append({"id": tx, "action": action, "item_key": f"item_{rng.randint(1,9)}",
                        "quantity": rng.randint(1, 50), "unit_value": rng.randint(500, 80000),
                        "character": n})
            tx += 1
    return summarize_contributions(log)
