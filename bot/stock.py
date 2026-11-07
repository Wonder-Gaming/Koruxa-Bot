"""Clan bank stock targets.

Officers set a target quantity for key bank items; this compares the bank's
current amount against each target and flags what's low. Pure logic + helpers to
resolve a typed item name against the bank and to read current quantities.

Thresholds mirror the reference bot: stocked at/above 100% of target, critical
below 50%, low in between.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CRITICAL_BELOW = 0.5


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


@dataclass
class StockRow:
    name: str
    current: int
    target: int

    @property
    def ratio(self) -> float:
        return self.current / max(1, self.target)

    @property
    def needed(self) -> int:
        return max(0, self.target - self.current)

    @property
    def over(self) -> int:
        return max(0, self.current - self.target)

    @property
    def percent(self) -> float:
        return 100 * self.ratio

    @property
    def status(self) -> tuple[str, str]:
        if self.ratio >= 1:
            return "🟢", "Stocked"
        if self.ratio < CRITICAL_BELOW:
            return "🔴", "Critical"
        return "🟡", "Low"


_RANK = {"🔴": 0, "🟡": 1, "🟢": 2}


def current_quantity(bank_items: list[dict[str, Any]], item_key: str, name: str) -> int:
    """Sum the bank quantity for a target (match by item_key, else by name)."""
    key = _norm(item_key)
    nm = _norm(name)
    total = 0
    for item in bank_items or []:
        if (key and _norm(item.get("item_key")) == key) or (not key and nm and _norm(item.get("name")) == nm):
            total += int(item.get("quantity") or 0)
    return total


def resolve_item(bank_items: list[dict[str, Any]], query: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Find a bank item by typed name/key. Returns (exact-ish match, candidates)."""
    needle = _norm(query)
    if not needle:
        return None, []
    exact = [i for i in bank_items or [] if _norm(i.get("item_key")) == needle or _norm(i.get("name")) == needle]
    if exact:
        return exact[0], exact
    partial = [i for i in bank_items or [] if needle in _norm(i.get("name")) or needle in _norm(i.get("item_key"))]
    partial.sort(key=lambda i: len(_norm(i.get("name"))))
    return (partial[0] if len(partial) == 1 else None), partial[:10]


def build_rows_from_targets(targets: list[dict[str, Any]], bank_items: list[dict[str, Any]]) -> list[StockRow]:
    """targets: [{item_key, name, target}]. Current quantities come from the bank."""
    rows = [StockRow(t.get("name") or t.get("item_key") or "?",
                     current_quantity(bank_items, t.get("item_key", ""), t.get("name", "")),
                     int(t.get("target") or 1)) for t in targets]
    return sorted(rows, key=lambda r: (_RANK[r.status[0]], r.percent))


def build_rows(targets: list[tuple[str, int, int]]) -> list[StockRow]:
    """targets: (item name, current quantity, target quantity). Lowest stock first."""
    rows = [StockRow(name, cur, tgt) for name, cur, tgt in targets]
    return sorted(rows, key=lambda r: (_RANK[r.status[0]], r.percent))


# Demo board (no clan linked): realistic clan-supply items, fabricated numbers.
DEMO_TARGETS: list[tuple[str, int, int]] = [
    ("Overload Potion", 180, 1000), ("Cinderfury Elixir", 60, 500),
    ("Drakeflower Brew", 740, 800), ("Shark (cooked)", 2100, 2000),
    ("Prayer Potion", 95, 400), ("Runite Bar", 310, 600),
    ("Dragon Bones", 1500, 1500), ("Nature Rune", 12400, 10000),
    ("Vexite Ore", 44, 250), ("Ancient Shard", 8, 50),
]


def demo_rows() -> list[StockRow]:
    return build_rows(DEMO_TARGETS)
