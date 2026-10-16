"""Mastery planning: the quickest route to a total mastery level in a skill.

Total mastery = the sum of mastery levels of a skill's actions (e.g. Scorchwood
Tree needs 3,000 Woodcutting mastery). Each action earns its own mastery XP per
hour, and every level costs more than the last, so repeatedly training whichever
action's *next* level is quickest gives the fastest route (greedy is optimal
because each action's level costs only increase).

Which levels to train is chosen from *permanent* rates (gear, tools, perks; no
potions, brew, wells or clan boosts), so the target levels stay the same when
temporary boosts come and go. Boosts don't scale all actions equally: fast
actions hit the 1-second floor. The time is then shown both ways.

The mastery XP curve is shared by every action. The API only shows each
action's next threshold, so exact thresholds seen in the data are used where
known and a cubic fitted to 114 known levels (within 10 XP) elsewhere.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any

# Expert actions also need total skill mastery, which the API doesn't report
# ("unlocked" only checks the skill level). Confirmed in game (Oct 2026):
# level 120 -> 3,000, 135 -> 3,500, 150 -> 4,000.
MASTERY_GATES = [(150, 4000), (135, 3500), (120, 3000)]


def mastery_gate(level_req: int) -> int:
    return next((total for lvl, total in MASTERY_GATES if level_req >= lvl), 0)


# XP needed to reach mastery level L, with n = L - 1 (fitted to live data, Oct 2026).
_A, _B, _C, _D = 10.400000939, 31.199270948, 31.354281768, 0.922838917


def threshold(level: int, known: dict[int, int]) -> int:
    if level in known:
        return known[level]
    n = level - 1
    return round(_A * n**3 + _B * n**2 + _C * n + _D)


@dataclass
class ActionState:
    key: str
    label: str
    level: int
    xp: float
    rate: float  # mastery XP per hour at permanent speed (used to choose the route)
    rate_now: float  # mastery XP per hour with today's boosts
    start_level: int = 0
    gate: int = 0  # total mastery needed before it can be trained
    xp_gained: float = 0.0


@dataclass
class Gain:
    label: str
    start: int
    end: int
    hours_now: float
    hours_base: float
    gate: int


@dataclass
class Milestone:
    target: int
    gains: dict[str, Gain] = field(default_factory=dict)
    reached: bool = True
    single: Gain | None = None  # quickest route training just one action, for comparison

    @property
    def hours_now(self) -> float:
        return sum(g.hours_now for g in self.gains.values())

    @property
    def hours_base(self) -> float:
        return sum(g.hours_base for g in self.gains.values())


def plan(
    actions: list[dict[str, Any]], mastery: list[dict[str, Any]], skill: str, targets: list[int]
) -> tuple[int, list[Milestone]]:
    """actions: /me/actions for the skill; mastery: /me private.mastery.actions. Returns (current total, milestones)."""
    known = {m["level"] + 1: m["xp_next_level"] for m in mastery if m.get("xp_next_level")}
    # Mastery rows name actions "basic"; /me/actions uses "woodcutting:basic" (with "action": "basic").
    by_key = {m["action_key"]: m for m in mastery if m.get("skill") == skill}
    current_total = sum(m.get("level", 0) for m in by_key.values())

    states: list[ActionState] = []
    for a in actions:
        if not a.get("unlocked") or not a.get("duration_ms"):
            continue
        row = by_key.get(a.get("action")) or by_key.get(a["action_key"])
        m = row or {"level": 1, "xp": 0}
        per_cycle = a.get("mastery_xp_per_cycle") or 0
        rate_now = per_cycle * 3_600_000 / a["duration_ms"]
        rate = per_cycle * 3_600_000 / (a.get("permanent_duration_ms") or a["duration_ms"])
        # Already-trained actions are clearly unlocked; untrained expert ones wait for their gate.
        gate = 0 if row else mastery_gate(a.get("level_req") or 0)
        if rate > 0 and rate_now > 0:
            states.append(ActionState(
                a["action_key"], a.get("label", a["action_key"]), m["level"], m.get("xp", 0), rate, rate_now,
                m["level"], gate,
            ))

    start_states = [(s.level, s.xp) for s in states]

    def single(target: int) -> Gain | None:
        """The quickest way to `target` training only one already-unlocked action."""
        need = target - current_total
        best = None
        for s, (level, xp) in zip(states, start_states):
            if s.gate > current_total or need <= 0:
                continue
            gained = max(threshold(level + need, known) - xp, 0)
            g = Gain(s.label, level, level + need, gained / s.rate_now, gained / s.rate, 0)
            if best is None or g.hours_now < best.hours_now:
                best = g
        return best

    def cost(s: ActionState) -> float:
        return max(threshold(s.level + 1, known) - s.xp, 0) / s.rate

    heap: list[tuple[float, int]] = []
    waiting = set(range(len(states)))
    total = current_total

    def release() -> None:
        for i in sorted(waiting):
            if states[i].gate <= total:
                waiting.discard(i)
                heapq.heappush(heap, (cost(states[i]), i))

    release()
    milestones = []
    for target in sorted(set(targets)):
        while total < target and heap:
            _, i = heapq.heappop(heap)
            s = states[i]
            s.xp_gained += max(threshold(s.level + 1, known) - s.xp, 0)
            s.xp = threshold(s.level + 1, known)
            s.level += 1
            total += 1
            heapq.heappush(heap, (cost(s), i))
            if waiting:
                release()
        milestone = Milestone(target, reached=total >= target, single=single(target))
        for s in states:
            if s.level > s.start_level:
                milestone.gains[s.key] = Gain(
                    s.label, s.start_level, s.level, s.xp_gained / s.rate_now, s.xp_gained / s.rate, s.gate
                )
        milestones.append(milestone)
    return current_total, milestones


def duration_text(hours: float) -> str:
    minutes = round(hours * 60)
    days, minutes = divmod(minutes, 60 * 24)
    h, m = divmod(minutes, 60)
    if days:
        return f"{days}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"
