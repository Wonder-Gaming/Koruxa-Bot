"""Builds gear/augment guides from the cached wiki data (bot/wiki_cache.py).

Pure logic: parse item stat strings, group by slot, and pick the best item per
slot for a combat style. No Discord here, so it's testable and previewable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

STYLES = {"melee": "Melee", "ranged": "Ranged", "magic": "Magic"}
# Armour + weapon slots, in a sensible display order.
SLOT_ORDER = ["Main Hand (two-handed)", "Main Hand", "Off Hand", "Head", "Chest",
              "Legs", "Hands", "Feet", "Back", "Cape", "Neck", "Ring"]
_ENCHANT = re.compile(r"[⭐🌙☀]")
_STAT = re.compile(r"([A-Za-z][A-Za-z ]*?)\s*([+\-]?\d+)(%?)")


def parse_stats(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in str(text or "").split("·"):
        m = _STAT.search(part)
        if m:
            name = m.group(1).strip()
            out[name + ("%" if m.group(3) else "")] = int(m.group(2))
    return out


@dataclass
class GearItem:
    key: str
    name: str
    slot: str
    level: int | None
    rarity: str
    weapon_type: str | None
    stats: dict[str, int]
    market: int | None

    def atk(self, style_label: str) -> int:
        return self.stats.get(style_label, 0)

    @property
    def bulk(self) -> int:  # defensive value, for tie-breaks
        return self.stats.get("Defence", 0) + self.stats.get("HP", 0)


def load_gear(rows: list[tuple[str]]) -> list[GearItem]:
    """rows: [(data_json,)] from wiki_items. Keeps equippable, non-enchant-variant items."""
    out = []
    for (dj,) in rows:
        d = json.loads(dj)
        slot = d.get("equip_slot")
        if not slot:
            continue
        name = str(d.get("name") or "")
        if _ENCHANT.search(name) or re.search(r"_(?:star|moon|sun)(?:_e\d+)?$", d.get("item_key", "")):
            continue  # skip enchant variants; recommend the base item (enchant it yourself)
        out.append(GearItem(
            key=d.get("item_key", ""), name=name, slot=slot, level=d.get("required_level"),
            rarity=d.get("rarity") or "", weapon_type=d.get("weapon_type"),
            stats=parse_stats(d.get("stats_text", "")), market=d.get("market_price")))
    return out


def best_in_slot(items: list[GearItem], style: str, max_level: int | None = None) -> dict[str, GearItem]:
    """Best item per slot for a damage style, optionally capped at max_level.

    Weapons must grant the style's attack stat; armour is picked by the style's
    attack stat too (so e.g. Magic sets use magic gear), tie-broken by bulk.
    """
    label = STYLES[style]
    best: dict[str, GearItem] = {}
    for it in items:
        if max_level is not None and it.level is not None and it.level > max_level:
            continue
        is_weapon = "Hand" in it.slot
        score = it.atk(label)
        if is_weapon and score <= 0:
            continue  # wrong-style weapon
        if not is_weapon and score <= 0 and it.bulk <= 0:
            continue
        cur = best.get(it.slot)
        key = (score, it.bulk, -(it.level or 0))
        if cur is None or key > (cur.atk(label), cur.bulk, -(cur.level or 0)):
            best[it.slot] = it
    return best


STYLE_EMOJI = {"melee": "⚔️", "ranged": "🏹", "magic": "🔮"}
BRACKETS = [20, 40, 60, 80, 100, 120, 150, 200]  # "up to level N" (200 = endgame)


def fmt_stats(stats: dict[str, int]) -> str:
    return " · ".join(f"{k} {'+' if v >= 0 else ''}{v}" for k, v in stats.items()) or "—"


def _bis_messages(items: list[GearItem], style: str) -> list[dict[str, str]]:
    label = STYLES[style]
    msgs: list[dict[str, str]] = []
    prev_sig = None
    for cap in BRACKETS:
        best = best_in_slot(items, style, cap)
        if not best:
            continue
        slots = ordered_slots(best)
        sig = tuple((s, best[s].key) for s in slots)
        if sig == prev_sig:
            continue  # no gear changed since the last bracket
        prev_sig = sig
        lines, atk, dfc, hp = [], 0, 0, 0
        for slot in slots:
            it = best[slot]
            lv = f"L{it.level}" if it.level is not None else "—"
            lines.append(f"**{slot}** · {it.name} ({lv})\n{fmt_stats(it.stats)}")
            atk += it.atk(label); dfc += it.stats.get("Defence", 0); hp += it.stats.get("HP", 0)
        desc = "\n".join(lines) + f"\n\n**Set totals** — {label} +{atk} · Defence +{dfc} · HP +{hp}"
        cap_name = "endgame" if cap >= 200 else f"up to level {cap}"
        msgs.append({"title": f"{label} · {cap_name}", "description": desc[:4096]})
    return msgs


def _upgrade_messages(items: list[GearItem], style: str) -> list[dict[str, str]]:
    label = STYLES[style]
    by_slot: dict[str, list[GearItem]] = {}
    for it in items:
        is_weapon = "Hand" in it.slot
        if is_weapon and it.atk(label) <= 0:
            continue
        if not is_weapon and it.atk(label) <= 0 and it.bulk <= 0:
            continue
        by_slot.setdefault(it.slot, []).append(it)

    blocks = []
    for slot in SLOT_ORDER:
        if slot not in by_slot:
            continue
        cand = sorted(by_slot[slot], key=lambda x: (x.level or 0, x.atk(label) * 1000 + x.bulk))
        path, best_score = [], -1
        for it in cand:
            score = it.atk(label) * 1000 + it.bulk
            if score > best_score:
                best_score = score
                path.append(it)
        rows = [f"`{('L' + str(it.level)) if it.level is not None else '—':>5}` {it.name} · {fmt_stats(it.stats)}"
                for it in path[-8:]]
        blocks.append(f"**{slot}**\n" + "\n".join(rows))

    msgs, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > 3900:
            msgs.append({"title": f"{label} upgrade paths", "description": cur})
            cur = ""
        cur = (cur + "\n\n" + b) if cur else b
    if cur:
        msgs.append({"title": f"{label} upgrade paths", "description": cur})
    return msgs


def _augment_messages(augments: list[dict]) -> list[dict[str, str]]:
    msgs = []
    for style in ("melee", "ranged", "magic"):
        rows = [a for a in augments if a.get("style") == style]
        rows.sort(key=lambda a: -(a["values"][-1] if a.get("values") else 0))
        if not rows:
            continue
        lines = []
        for a in rows:
            vals = a.get("values") or []
            prog = f"L1 {vals[0]} → L{len(vals)} {vals[-1]}" if vals else ""
            lines.append(f"{a.get('emoji', '')} **{a['name']}** — {prog}"
                         + (f"\n{a['desc']}" if a.get("desc") else ""))
        msgs.append({"title": f"{STYLES[style]} augments", "description": "\n".join(lines)[:4096]})
    return msgs


def build_guides(items: list[GearItem], augments: list[dict]) -> list[dict]:
    """All guides: each is {key, emoji, title, messages:[{title, description}]}."""
    guides = []
    for style in ("melee", "ranged", "magic"):
        guides.append({"key": f"bis_{style}", "emoji": STYLE_EMOJI[style],
                       "title": f"Best-in-Slot — {STYLES[style]}", "messages": _bis_messages(items, style)})
    for style in ("melee", "ranged", "magic"):
        guides.append({"key": f"upgrade_{style}", "emoji": "📈",
                       "title": f"Upgrade Paths — {STYLES[style]}", "messages": _upgrade_messages(items, style)})
    guides.append({"key": "augments", "emoji": "💥", "title": "Combat Augments",
                   "messages": _augment_messages(augments)})
    return [g for g in guides if g["messages"]]


def ordered_slots(best: dict[str, GearItem]) -> list[str]:
    # Prefer a two-hander over main+off if it's clearly present.
    slots = [s for s in SLOT_ORDER if s in best]
    if "Main Hand (two-handed)" in best and "Main Hand" in best:
        th, mh = best["Main Hand (two-handed)"], best["Main Hand"]
        style_th = max(th.stats.values(), default=0)
        style_mh = max(mh.stats.values(), default=0)
        drop = "Main Hand" if style_th >= style_mh else "Main Hand (two-handed)"
        slots = [s for s in slots if s != drop and not (s == "Off Hand" and drop == "Main Hand")]
    return slots
