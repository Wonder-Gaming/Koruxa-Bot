"""Clan boss upgrades: expected damage per attempt, and which upgrade gives the most damage per scroll.

/me "boss" has each upgrade's level but not what it does or costs. The model:
- Max hit = (5 + Damage) x 1.28^Power. Verified: it matches the API's boss_max_hit exactly for every linked
  player (Oct 2026). The wiki's update notes confirm x1.28 per Power level (the community calculator still
  used the old x1.24) and the Power cost steps.
- Each hit rolls between the minimum (Precision % of the max hit) and the max hit, one hit per second for
  30 + Time seconds.
- Crit: Crit % chance (max 25, per the wiki), crits deal x(1.5 + 0.15 x Devastation).
- Rage: builds up during a fight to a cap of 10% + 5% per Rage level; counted at 75% of the cap on average
  (the game's own old estimate for Expert Tier decks).
- Scroll cost of level n: Damage 5n, Time 8n, Crit 8n, Devastation 12n, Rage 12n, Precision 25n (from a
  community calculator), Power 12n with x3 from 51, x8 from 71, x20 from 91 (wiki).
- Caps (confirmed in game, Nov 2026): Precision 40, Rage 15, Devastation 20, Crit 25, Time 30; Damage and
  Power have none.
Damage per scroll compares the upgrades fairly because every factor multiplies the same total.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

UPGRADES = ["damage", "power", "precision", "time", "crit", "devastation", "rage"]
LABELS = {"damage": "Damage", "power": "Power", "precision": "Precision", "time": "Time", "crit": "Crit",
          "devastation": "Devastation", "rage": "Rage"}
EMOJI = {"damage": "⚔️", "power": "💪", "precision": "🎯", "time": "⏱️", "crit": "💥", "devastation": "⚡",
         "rage": "🔥"}
CAPS = {"precision": 40, "time": 30, "crit": 25, "devastation": 20, "rage": 15}
COST_PER_LEVEL = {"damage": 5, "time": 8, "crit": 8, "devastation": 12, "rage": 12, "precision": 25, "power": 12}
RAGE_AVERAGE = 0.75  # share of the rage cap reached on average over a fight


@dataclass(frozen=True)
class Upgrades:
    damage: int = 0
    power: int = 0
    precision: int = 0
    time: int = 0
    crit: int = 0
    devastation: int = 0
    rage: int = 0

    @classmethod
    def from_me(cls, me: dict) -> Upgrades:
        b = me.get("boss") if isinstance(me.get("boss"), dict) else {}
        return cls(**{u: int(b.get(f"{u}_level") or 0) for u in UPGRADES})

    def max_hit(self) -> float:
        return (5 + self.damage) * 1.28 ** self.power

    def per_attempt(self, bonus: float = 0.0) -> float:
        """Expected damage of one attempt. `bonus`: other % damage bonuses as a fraction (e.g. research)."""
        avg_roll = (1 + min(self.precision, 100) / 100) / 2
        crit = 1 + min(self.crit, 100) / 100 * (1.5 + 0.15 * self.devastation - 1)
        rage = 1 + RAGE_AVERAGE * (0.10 + 0.05 * self.rage)
        return self.max_hit() * avg_roll * crit * rage * (1 + bonus) * (30 + self.time)

    def cost(self, upgrade: str) -> int | None:
        """Scrolls for the next level of `upgrade` (None when it's maxed)."""
        n = getattr(self, upgrade) + 1
        if upgrade in CAPS and n > CAPS[upgrade]:
            return None
        base = COST_PER_LEVEL[upgrade] * n
        if upgrade == "power":
            base *= 20 if n >= 91 else 8 if n >= 71 else 3 if n >= 51 else 1
        return base

    def next(self, upgrade: str) -> Upgrades:
        return replace(self, **{upgrade: getattr(self, upgrade) + 1})


@dataclass
class Option:
    upgrade: str
    level: int  # the level it goes to
    cost: int
    gain: float  # damage multiplier, e.g. 1.28

    @property
    def per_1k(self) -> float:
        """% more damage per 1,000 scrolls (compounding-fair: log scale)."""
        return (math.exp(math.log(self.gain) * 1000 / self.cost) - 1) * 100


def options(u: Upgrades) -> list[Option]:
    """Every upgrade's next level, best damage per scroll first."""
    now = u.per_attempt()
    out = []
    for name in UPGRADES:
        cost = u.cost(name)
        if cost is None:
            continue
        nxt = u.next(name)
        out.append(Option(name, getattr(nxt, name), cost, nxt.per_attempt() / now))
    return sorted(out, key=lambda o: -math.log(o.gain) / o.cost)


@dataclass
class Plan:
    steps: list[Option]  # in buying order
    end: Upgrades
    spent: int
    gain: float


def plan(u: Upgrades, budget: int | None = None, max_steps: int = 25) -> Plan:
    """Buy the best damage-per-scroll level, one at a time, until the budget (or max_steps) runs out."""
    steps: list[Option] = []
    spent, cur = 0, u
    while len(steps) < max_steps:
        affordable = [o for o in options(cur) if budget is None or spent + o.cost <= budget]
        if not affordable:
            break
        best = affordable[0]
        steps.append(best)
        spent += best.cost
        cur = cur.next(best.upgrade)
    return Plan(steps, cur, spent, cur.per_attempt() / u.per_attempt())


def maxed(u: Upgrades) -> list[str]:
    return [name for name in UPGRADES if name in CAPS and getattr(u, name) >= CAPS[name]]
