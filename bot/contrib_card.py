"""Draws the clan bank contribution board as a PNG, in the same style as the
clan-bank card (bot/render.py helpers): deep-purple card, blue accent, ROW_SHADE
rows, gold/green values. Members ranked by net value contributed.
"""

from __future__ import annotations

import io

from .render import (BLUE, BODY, FAINT, GOLD, GREEN, HEAD, MUTED, PAD, ROW_SHADE,
                     TEXT, W, _card, _font, _footer, _header)

RED = (239, 109, 110)


def _c(value: int) -> str:
    """Compact coins: 1.2m, 940k, 512."""
    v = int(value)
    neg = v < 0
    v = abs(v)
    if v >= 1_000_000_000:
        s = f"{v / 1e9:.1f}b"
    elif v >= 1_000_000:
        s = f"{v / 1e6:.1f}m"
    elif v >= 1_000:
        s = f"{v / 1e3:.1f}k"
    else:
        s = str(v)
    s = s.replace(".0", "")
    return ("−" + s) if neg else s


def render_contrib_card(summary, clan_name: str) -> bytes:
    members = summary.members[:9]
    img, d = _card(BLUE)
    shown = f"top {len(members)} of {len(summary.members)}" if len(summary.members) > len(members) else f"{len(members)} players"
    _header(img, d, "🏦", "Bank Contributions",
            f"{clan_name} · {shown} · {_c(summary.total_deposited)} in · {_c(summary.total_withdrawn)} out", BLUE)

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
        d.text((x0 + 92, mid + 18), f"In {_c(m.deposited)}   ·   Out {_c(m.withdrawn)}   ·   {m.transactions} tx",
               font=_font(22), fill=MUTED, anchor="lm")
        taker = m.net < 0
        value = f"{'−' if taker else '+'}{_c(abs(m.net))}"
        d.text((W - PAD - 30, mid), value, font=_font(32, "Bold"), fill=RED if taker else GREEN, anchor="rm")

    _footer(d, "Net = value deposited − withdrawn, at wiki market prices, grouped per player.")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()
