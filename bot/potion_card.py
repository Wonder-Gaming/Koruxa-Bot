"""Draws the weekly potion-usage board as a PNG, in the same visual style as the
highscores / clan-bank cards (bot/render.py helpers): deep-purple card, accent
bar, ROW_SHADE rows, gold values. Call via asyncio.to_thread.
"""

from __future__ import annotations

import io

from PIL import ImageDraw  # noqa: F401  (kept for parity / type hints)

from .render import (BODY, FAINT, FOOT, GOLD, GREEN, HEAD, MUTED, NAME, PAD, ROW_SHADE,
                     TEXT, W, H, _card, _font, _footer, _header, _paste_emoji)


def _n(value: int) -> str:
    return f"{int(value):,}"


def render_potion_card(summary, clan_name: str) -> bytes:
    members = summary.members[:9]
    img, d = _card(GREEN)
    week = summary.week_start.strftime("%b %d")
    _header(img, d, "🧪", "Weekly Potion Usage",
            f"{clan_name} · week of {week} · {len(summary.members)} players · {_n(summary.total)} net", GREEN)

    x0 = PAD + 20
    top = PAD + HEAD
    n = max(1, len(members))
    row_h = min(96, BODY // n)
    for i, m in enumerate(members):
        y = top + i * row_h
        d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + row_h - 10), 14, fill=ROW_SHADE)
        mid = y + (row_h - 10) // 2
        d.text((x0 + 10, mid), f"#{i + 1}", font=_font(26, "Bold"), fill=FAINT, anchor="lm")
        d.text((x0 + 92, mid - 17), m.name, font=_font(30, "SemiBold"), fill=TEXT, anchor="lm")
        positives = [(k, q) for k, q in m.top(6) if q > 0][:3]
        breakdown = "   ".join(f"{summary.name_of(k)} {_n(q)}" for k, q in positives)
        if not breakdown:
            breakdown = "net contributor (banked ≥ drawn)"
        d.text((x0 + 92, mid + 18), breakdown, font=_font(22), fill=MUTED, anchor="lm")
        if m.total <= 0:
            value, colour = f"+{_n(-m.total)} banked", GREEN
        else:
            value, colour = f"{_n(m.total)} net", GOLD
        d.text((W - PAD - 30, mid), value, font=_font(32, "Bold"), fill=colour, anchor="rm")

    _footer(d, "Net = bank withdrawals − deposits this week, grouped per player (linked characters combined).")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()
