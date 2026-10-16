"""XP gains: the latest snapshot compared with the XP history from N days ago."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .db import Database, Snapshot


@dataclass
class Gains:
    since_day: str  # the history day the gains are measured from
    partial: bool  # history doesn't reach back the whole period yet
    total_xp: int = 0
    levels: int = 0
    skills: dict[str, tuple[int, int]] = field(default_factory=dict)  # key -> (xp gained, levels gained)


async def gains(db: Database, snap: Snapshot, days: int) -> Gains | None:
    """Gains over the last `days` days, or None if there's no earlier data yet."""
    today = datetime.now(timezone.utc).date()
    target = (today - timedelta(days=days)).isoformat()
    base = await db.history_baseline(snap.character_id, target)
    if base is None or base.day >= today.isoformat():
        return None  # only today's data: nothing to compare against yet
    result = Gains(since_day=base.day, partial=base.day > target)
    for key, (level, xp) in snap.skills.items():
        old_level, old_xp = base.skills.get(key, [level, xp])
        if xp > old_xp or level > old_level:
            result.skills[key] = (xp - old_xp, level - old_level)
            result.levels += max(level - old_level, 0)
    result.total_xp = sum(xp for xp, _ in result.skills.values())
    return result
