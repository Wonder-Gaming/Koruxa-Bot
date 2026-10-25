"""Render every image the bot posts, from made-up data, for the README screenshots.

The players, clan and numbers are all fictional. The images go through the
same drawing code the bot uses, so they look exactly like the real posts.

Run from the project folder (the bot's container has the fonts and libraries):

    docker compose run --rm --no-deps -v "$PWD/scripts:/app/scripts:ro" \
        -v "$PWD/docs/images:/out" bot python -m scripts.demo_images /out
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone

from bot.cogs.clan import (
    activity_image, bank_image, boss_image, clan_level_card, damage_image, institute_cards, pool_thanks_card,
    recap_image, research_done_card, research_started_card,
)
from bot.cogs.highscores import build_board
from bot.db import Snapshot
from bot.render import Gainer, LevelUp, render_board, render_level_ups, render_top_gainers
from bot.skills import SKILL_GROUPS, skill_emoji, skill_name

NOW = datetime(2026, 10, 19, 9, 30, tzinfo=timezone.utc)  # a Monday, just after the weekly reset
CLAN = {"id": 1, "name": "Mossy Hollow", "tag": "MOSS", "member_count": 18}
PLAYERS = ["Riftbreaker", "Mossy", "Pudge", "TotoroHero", "Mayhem", "Grindset", "Lil Nugget", "Fernwick"]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def players() -> list[Snapshot]:
    rng = random.Random(7)
    keys = [k for _, _, ks in SKILL_GROUPS for k in ks]
    result = []
    for i, name in enumerate(PLAYERS):
        skills = {k: [(lv := rng.randint(40, 120)), lv**3 * 25] for k in keys}
        result.append(Snapshot(
            i + 1, 1000 + i, name, CLAN["tag"], skills, iso(NOW),
            total_level=sum(lv for lv, _ in skills.values()),
            total_xp=sum(xp for _, xp in skills.values()),
            combat_level=rng.randint(60, 120),
        ))
    return result


def highscores(out: str) -> None:
    roster = players()
    gains = {p.character_id: p.total_xp // (60 + 9 * i) for i, p in enumerate(roster)}
    for board in ("Overall", "Gathering"):
        png = render_board(build_board(board, roster, 15, False, gains, f"[{CLAN['tag']}]"))
        save(out, f"highscores-{board.lower()}.png", png)


def feed(out: str) -> None:
    ups = [
        LevelUp("Riftbreaker", skill_emoji("woodcutting"), skill_name("woodcutting"), 98, 99, True),
        LevelUp("Riftbreaker", skill_emoji("fletching"), skill_name("fletching"), 71, 72, False),
        LevelUp("Pudge", skill_emoji("cooking"), skill_name("cooking"), 84, 86, False),
        LevelUp("Mayhem", skill_emoji("slayer"), skill_name("slayer"), 59, 60, False),
    ]
    save(out, "level-ups.png", render_level_ups(ups, f"{NOW:%d %b %Y %H:%M} UTC"))

    gainers = [
        Gainer("Grindset", "+4.8M", 9, skill_emoji("mining"), skill_name("mining"), "+2.1M"),
        Gainer("Mossy", "+3.2M", 6, skill_emoji("fishing"), skill_name("fishing"), "+1.4M"),
        Gainer("TotoroHero", "+2.7M", 5, skill_emoji("smithing"), skill_name("smithing"), "+980K"),
        Gainer("Fernwick", "+1.9M", 4, skill_emoji("herblore"), skill_name("herblore"), "+760K"),
        Gainer("Lil Nugget", "+1.1M", 3, skill_emoji("thieving"), skill_name("thieving"), "+520K"),
    ]
    week_start, week_end = NOW - timedelta(days=7), NOW - timedelta(days=1)
    save(out, "top-gainers.png", render_top_gainers(
        gainers, "Weekly top gainers",
        f"XP gained {week_start:%a %d %b} – {week_end:%a %d %b} · /gains for your own",
        f"Week ended {NOW:%d %b %Y} 00:00 UTC",
    ))


def boss(out: str) -> None:
    def part(hp: float, max_hp: float, shield: float) -> dict:
        return {"hp": hp, "max_hp": max_hp, "shield": shield, "shield_max": max_hp / 4}

    started = NOW - timedelta(hours=1, minutes=12)
    live = {
        "tier": 42, "max_tier_unlocked": 45, "status": "alive", "started_at": iso(started),
        "hp": {"current": 512e9, "max": 880e9, "pct": 58},
        "parts": {
            "head": part(140e9, 220e9, 0), "body": part(230e9, 330e9, 41e9),
            "arms": part(62e9, 150e9, 0), "legs": part(80e9, 180e9, 12e9),
        },
        "window": {"attempts_per_member": 6, "hours": 4, "number": 0,
                   "next_reset_at": iso(started + timedelta(hours=4))},
    }
    save(out, "clan-boss.png", boss_image(live, {"clan": CLAN}))

    # Damage on this boss: weekly totals now, minus the snapshot taken at the spawn.
    hits = {"Grindset": 118e9, "Mayhem": 74e9, "Riftbreaker": 61e9, "Mossy": 39e9, "TotoroHero": 27e9,
            "Pudge": 22e9, "Fernwick": 15e9, "Lil Nugget": 12e9}
    ids = {name: str(i) for i, name in enumerate(hits)}
    baseline = {ids[n]: 900e9 - i * 80e9 for i, n in enumerate(hits)}
    dmg = {
        "boss": "demo", "baseline_at": iso(started + timedelta(seconds=40)), "updated_at": iso(NOW),
        "names": {cid: n for n, cid in ids.items()}, "baseline": baseline, "carried": {},
        "latest": {ids[n]: baseline[ids[n]] + v for n, v in hits.items()},
    }
    save(out, "boss-damage.png", damage_image(dmg, live, {"clan": CLAN}))


def bank(out: str) -> None:
    items = [
        ("Worldtree Log", 2_400, 18_500_000, "epic"), ("Aetherite Rune", 4_200, 9_240_000, "rare"),
        ("Shalore Bar", 3_100, 6_400_000, "rare"), ("Cinderpine Log", 950, 4_750_000, "epic"),
        ("Crimsrite Ore", 6_300, 2_520_000, "uncommon"), ("Ironbark Log", 25_000, 1_250_000, "uncommon"),
        ("Fluxite Rune", 1_400, 1_120_000, "uncommon"), ("Copite Bar", 9_000, 450_000, "common"),
    ]
    log = [
        ("deposit", "Grindset", "Ironbark Log", 2_500, 125_000, 18), ("withdraw", "Pudge", "Fluxite Rune", 40, 32_000, 41),
        ("deposit", "Mossy", "Crimsrite Ore", 600, 240_000, 75), ("deposit", "Fernwick", "Worldtree Log", 120, 600_000, 130),
        ("withdraw", "Mayhem", "Cinderpine Log", 25, 125_000, 190),
        ("deposit", "TotoroHero", "Shalore Bar", 300, 660_000, 260),
    ]
    data = {
        "clan": CLAN, "generated_at": iso(NOW),
        "coins": 48_200_000, "items_value": sum(v for _, _, v, _ in items), "item_count": 58_000,
        "augment_count": 14, "augment_vault_cap": 50, "enchant_queue": [],
        "requests": [{"character": "Lil Nugget", "item_key": "fluxite_rune", "name": "Fluxite Rune", "quantity": 20,
                      "value": 16_000, "at": iso(NOW - timedelta(minutes=6))}],
        "tabs": [{"name": "Main"}, {"name": "Gear"}, {"name": "Supplies"}],
        "items": [{"name": n, "quantity": q, "total_value": v, "rarity": r} for n, q, v, r in items],
        "log": [{"action": a, "character": who, "name": n, "item_key": n.lower().replace(" ", "_"), "quantity": q,
                 "value": v, "at": iso(NOW - timedelta(minutes=m))} for a, who, n, q, v, m in log],
    }
    save(out, "clan-bank.png", bank_image(data))
    save(out, "bank-activity.png", activity_image(data))


def clan_progress(out: str) -> None:
    data = {
        "clan": CLAN,
        "xp": {"level": 12, "xp_total": 84_300_000, "xp_to_next": 9_700_000, "progress_pct": 3, "max_level": 50},
        "levels": [{"level": 11, "max_members": 20}, {"level": 12, "max_members": 22}],
    }
    save(out, "clan-level.png", clan_level_card(data, 11, 12))

    node = {
        "node_key": "clan_shared_knowledge", "name": "Shared Knowledge", "max_level": 25, "bonus_type": "clan_xp",
        "bonus_per_level": 0.005, "bonus_now": 0.06, "description": "Every clan member absorbs a little more from the world. +0.5% XP per level for all members.",
        "next": {"target_level": 13, "time_sec": 9 * 3600 + 40 * 60,
                 "materials": [{"qty": 52000, "item_name": "Aetherite Rune"}, {"qty": 41000, "item_name": "Basic Log"}]},
    }
    save(out, "research-complete.png", research_done_card({"clan": CLAN}, node, 12))
    save(out, "research-started.png", research_started_card(
        {"clan": CLAN, "speed_bonus_pct": 4}, "Shared Knowledge level 13", node, NOW + timedelta(hours=9, minutes=40)))

    week = (NOW - timedelta(days=7)).date().isoformat()
    recap = {
        "clan": CLAN, "xp": data["xp"],
        "weekly": [{"week_start": week, "xp": 6_900_000, "quests": 214, "boss_damage": 3.1e12,
                    "bank_value": 61_000_000, "firepit_hours": 96, "active_members": 16}],
        "weekly_members": [
            {"week_start": week, "character": name, "xp": xp, "quests": q, "boss_damage": dmg, "firepit_hours": fh}
            for name, xp, q, dmg, fh in [
                ("Grindset", 1_450_000, 31, 6.2e11, 14), ("Mossy", 1_120_000, 28, 4.9e11, 12),
                ("Riftbreaker", 980_000, 25, 5.5e11, 9), ("TotoroHero", 760_000, 22, 3.8e11, 11),
                ("Pudge", 610_000, 19, 2.7e11, 6),
            ]
        ],
    }
    save(out, "clan-week.png", recap_image(recap, NOW.replace(hour=0, minute=0)))


def institute(out: str) -> None:
    def mats(*rows):  # (name, filled or None, needed)
        return [{"item_name": n, "filled": f, "needed": q, "pct": round(100 * (f or 0) / q, 1), "qty": q} for n, f, q in rows]

    nodes = [
    {"node_key": "clan_shared_knowledge", "name": "Shared Knowledge", "level": 12, "max_level": 25, "bonus_type": "clan_xp", "bonus_per_level": 0.005, "bonus_now": 0.06,
     "description": "Every clan member absorbs a little more from the world. +0.5% XP per level for all members.", "next": {"target_level": 13, "time_sec": 9 * 3600 + 40 * 60}},
    {"node_key": "clan_institutional_discipline", "name": "Institutional Discipline", "level": 8, "max_level": 25, "bonus_type": "clan_research_speed", "bonus_per_level": 0.002, "bonus_now": 0.016,
     "description": "Institute efficiency spreads through the ranks. +0.2% research speed per level for all members.", "next": {"target_level": 9, "time_sec": 6 * 3600}},
    {"node_key": "clan_collective_power", "name": "Collective Power", "level": 3, "max_level": 25, "bonus_type": "clan_boss_damage_shared", "bonus_per_level": 0.003, "bonus_now": 0.009,
     "description": "The clan hits harder together. +0.3% clan boss damage per level.", "next": {"target_level": 4, "time_sec": 4 * 3600}},
    {"node_key": "clan_eternal_flame", "name": "Eternal Flame", "level": 1, "max_level": 2, "bonus_type": "clan_firepit_stage", "bonus_per_level": 1, "bonus_now": 1,
     "description": "A grand clan project to master the firepit. Level 1 unlocks the Inferno stage (85%+ heat, +8% XP for stokers), level 2 the Wildfire stage (95%+, +10%). Members pool coins and high-tier logs to fuel the research; once the pool is full the timer runs 7 days (level 1) or 14 days (level 2).", "next": {"target_level": 2, "time_sec": 14 * 86400}},
    {"node_key": "clan_fortune", "name": "Clan Fortune", "level": 0, "max_level": 25, "bonus_type": "clan_drop_rate", "bonus_per_level": 0.003, "bonus_now": 0,
     "description": "Luck flows through the clan. Each level gives all clan members +0.3% Drop Rate on monster drops and +0.3% Double Drop on all skills (stacks with Fortunes Favor).", "next": {"target_level": 1, "time_sec": 3600}},
    ]
    active = {"node_key": "clan_shared_knowledge", "name": "Shared Knowledge", "target_level": 13, "is_paused": False,
              "pool_full": False, "pool_pct": 83.4, "time_sec": 9 * 3600 + 40 * 60, "time_remaining_sec": 0,
              "materials": mats(("Aetherite Rune", 52000, 52000), ("Basic Log", 41000, 41000), ("Glowroot", 18700, 24500),
                                ("Dustite Bar", 7100, 16300), ("Bones", 22900, 30600)),
              "contributors": [{"name": n, "item_name": i, "qty": q} for n, i, q in (
                  ("Grindset", "Aetherite Rune", 26000), ("Mossy", "Basic Log", 19000), ("Fernwick", "Glowroot", 14000),
                  ("Riftbreaker", "Bones", 9100), ("Pudge", "Dustite Bar", 4300), ("Mossy", "Bones", 0))]}
    data = {
        "clan": CLAN, "speed_bonus_pct": 4, "nodes": nodes, "active": active,
        "paused": [{"node_key": "clan_institutional_discipline", "name": "Institutional Discipline", "target_level": 9,
                    "pool_full": False, "pool_pct": 41.0, "time_remaining_sec": 0,
                    "materials": mats(("Shalore Bar", 3400, 8100), ("Ironbark Log", 9000, 21600), ("Fluxite Rune", 0, 13500))}],
        "lifetime_top": [{"name": n, "qty": q} for n, q in (
            ("Grindset", 482100), ("Mossy", 398700), ("Riftbreaker", 311550), ("Fernwick", 226400), ("TotoroHero", 183200),
            ("Pudge", 129050), ("Lil Nugget", 81200), ("Mayhem", 55400), ("Grindset_Supreme", 23100), ("Thornwick", 9400))],
    }
    for (png, _, _), name in zip(institute_cards(data), ["institute-donors.png", "institute-overview.png",
                                                          "institute-research.png"]):
        save(out, name, png)
    filled = {**active, "pool_full": True, "time_remaining_sec": active["time_sec"]}
    save(out, "institute-pool-filled.png", pool_thanks_card(data, filled))


def save(out: str, name: str, png: bytes | None) -> None:
    if png is None:
        raise SystemExit(f"{name}: nothing rendered")
    with open(os.path.join(out, name), "wb") as f:
        f.write(png)
    print("wrote", name)


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/images"
    os.makedirs(out, exist_ok=True)
    for step in (highscores, feed, boss, bank, clan_progress, institute):
        step(out)


if __name__ == "__main__":
    main()
