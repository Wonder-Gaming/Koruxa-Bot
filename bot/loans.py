"""Clan bank loan ledger, reconstructed from the bank log.

Koruxa has no explicit loan system, but high-value "sealed" items (enchant-style
variants marked star/moon/sun or ⭐🌙☀️) are lent out of the bank and expected
back. This treats a sealed-item withdrawal/gift as a loan and a matching deposit
by the same member as a return. Pure logic (no Discord); a demo generator covers
the no-sealed-items-yet case.

Sealed detection is ported from the reference bot's isSealedBankItem.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Sealed = enchant-style variants: the name carries ⭐/🌙/☀️ and the key ends in
# _star/_moon/_sun (optionally + _e<level>). Matching the suffix/emoji precisely
# avoids false positives from plain materials like "sun_uncut" (Uncut Sunstone).
_SEAL_EMOJI = re.compile(r"[⭐🌙☀]")
_SEAL_SUFFIX = re.compile(r"_(?:star|moon|sun)(?:_e\d+)?$", re.I)
_SEAL_WORD = re.compile(r"(?:^|[_\s-])sealed?(?=$|[_\s-])", re.I)
CONSUMING_ACTIONS = {"withdraw", "give"}


def is_sealed_item(item_key: Any, name: Any = "") -> bool:
    key = str(item_key or "")
    name = str(name or "")
    return (bool(_SEAL_EMOJI.search(name))
            or bool(_SEAL_SUFFIX.search(key))
            or bool(_SEAL_WORD.search(f"{key} {name}")))


@dataclass
class Loan:
    item_key: str
    item_name: str
    borrower: str            # the player's representative character name
    borrowed: int            # gross withdrawn/gifted across the player's characters
    deposited: int           # gross deposited back across the player's characters
    first_at: datetime
    last_at: datetime

    @property
    def outstanding(self) -> int:
        return max(0, self.borrowed - self.deposited)

    @property
    def active(self) -> bool:
        return self.outstanding > 0


def _parse_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def reconstruct_loans(
    log: list[dict[str, Any]],
    char_owner: dict[int, int] | None = None,
    char_name: dict[int, str] | None = None,
) -> list[Loan]:
    """Reconstruct sealed-item loans, grouped PER PLAYER.

    Entries are grouped by the player who owns the character (char_owner:
    character_id -> owner id), so a sealed item a player moves between their own
    linked alts nets out (withdraw on one alt + deposit on another = no loan).
    Withdrawals/gifts add to borrowed; deposits add to deposited; outstanding is
    the clamped difference. Unlinked characters are grouped on their own.
    """
    char_owner = char_owner or {}
    char_name = char_name or {}
    ordered = sorted(log or [], key=lambda e: (_parse_at(e.get("at")) or _EPOCH, e.get("id") or 0))
    groups: dict[tuple[tuple[str, Any], str], dict[str, Any]] = {}

    for entry in ordered:
        key = str(entry.get("item_key", ""))
        name = str(entry.get("name") or key)
        if not is_sealed_item(key, name):
            continue
        action = str(entry.get("action", "")).lower()
        if action in CONSUMING_ACTIONS:
            kind = "borrow"
        elif action == "deposit":
            kind = "return"
        else:
            continue
        who = str(entry.get("character", "")).strip()
        when = _parse_at(entry.get("at"))
        try:
            qty = int(entry.get("quantity"))
        except (TypeError, ValueError):
            continue
        if not who or qty <= 0:
            continue
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
            gkey = ("name", who.casefold())

        acc = groups.setdefault((gkey, key), {
            "item_name": name, "borrowed": 0, "deposited": 0,
            "first_at": when or _EPOCH, "last_at": when or _EPOCH, "labels": Counter(),
        })
        label = (char_name.get(cid) if cid is not None else None) or who
        acc["labels"][label] += qty
        if kind == "borrow":
            acc["borrowed"] += qty
            if when:
                acc["last_at"] = max(acc["last_at"], when)
                acc["first_at"] = min(acc["first_at"], when) if acc["first_at"] != _EPOCH else when
        else:
            acc["deposited"] += qty

    loans = []
    for acc in groups.values():
        if acc["borrowed"] <= 0:
            continue
        borrower = acc["labels"].most_common(1)[0][0] if acc["labels"] else "Unknown"
        loans.append(Loan(
            item_key="", item_name=acc["item_name"], borrower=borrower,
            borrowed=acc["borrowed"], deposited=acc["deposited"],
            first_at=acc["first_at"], last_at=acc["last_at"],
        ))
    loans.sort(key=lambda ln: (0 if ln.active else 1, -ln.last_at.timestamp()))
    return loans


def summarize(loans: list[Loan]) -> dict[str, int]:
    active = [ln for ln in loans if ln.active]
    return {
        "active": len(active),
        "returned": sum(1 for ln in loans if not ln.active),
        "outstanding_items": sum(ln.outstanding for ln in active),
    }


# Demo ledger: fabricated sealed items (TLC has none banked right now).
def demo_loans() -> list[Loan]:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    return reconstruct_loans([
        {"id": 1, "at": (now - timedelta(days=6)).isoformat(), "action": "withdraw",
         "item_key": "dragon_scimitar_sun", "name": "Dragon Scimitar ☀️", "quantity": 1, "character": "Pudge"},
        {"id": 2, "at": (now - timedelta(days=5)).isoformat(), "action": "withdraw",
         "item_key": "vexite_platebody_moon", "name": "Vexite Platebody 🌙", "quantity": 1, "character": "Mossgrove"},
        {"id": 3, "at": (now - timedelta(days=4)).isoformat(), "action": "deposit",
         "item_key": "vexite_platebody_moon", "name": "Vexite Platebody 🌙", "quantity": 1, "character": "Mossgrove"},
        {"id": 4, "at": (now - timedelta(days=2)).isoformat(), "action": "give",
         "item_key": "sealed_bow_star", "name": "Sealed Bow ⭐", "quantity": 1, "character": "xX_Slayer_Xx"},
    ])
