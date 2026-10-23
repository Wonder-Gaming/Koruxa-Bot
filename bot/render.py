"""Draws the highscores boards as PNG images (Pillow).

Uses the bundled Inter font and Twemoji icons in /app/assets, so nothing is
downloaded at runtime. Rendering is CPU work, so callers should run it in a
thread (asyncio.to_thread).
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

ASSETS = os.environ.get("ASSETS_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets"))

BACKGROUND = (15, 13, 22)
CARD = (26, 22, 37)
ROW_SHADE = (35, 29, 49)
TEXT = (244, 244, 245)
NAME = (212, 212, 216)
MUTED = (161, 161, 170)
FAINT = (113, 113, 122)
MEDALS = [("🥇", "1ST", (251, 191, 36)), ("🥈", "2ND", (203, 213, 225)), ("🥉", "3RD", (217, 119, 6))]

# Every card is drawn on the same canvas, so Discord (which shrinks an image to fit a box of limited
# height) shows them all at the same scale.
W, H = 1600, 912
PAD, HEAD, COLH, ROW, FOOT = 36, 130, 64, 96, 70
BODY = H - PAD - HEAD - FOOT - PAD  # room between the header and the footer (640)
LABEL_W = 330


@dataclass
class Row:
    emoji: str
    label: str
    cells: list[tuple[str, str]]  # (player name, value text), best first; up to 3


@dataclass
class Board:
    title: str
    emoji: str
    accent: tuple[int, int, int]
    subtitle: str
    first_column: str
    rows: list[Row]
    footer: str


@lru_cache(maxsize=1)
def _font_bytes() -> bytes:
    with open(os.path.join(ASSETS, "fonts", "Inter.ttf"), "rb") as f:
        return f.read()


@lru_cache(maxsize=32)
def _font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(io.BytesIO(_font_bytes()), size)
    font.set_variation_by_name(weight)
    return font


@lru_cache(maxsize=128)
def _emoji(char: str, size: int) -> Image.Image | None:
    code = "-".join(f"{ord(c):x}" for c in char if ord(c) != 0xFE0F)
    path = os.path.join(ASSETS, "twemoji", f"{code}.png")
    if not os.path.exists(path):
        return None
    return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)


def _paste_emoji(img: Image.Image, char: str, size: int, xy: tuple[int, int]) -> None:
    icon = _emoji(char, size)
    if icon is not None:
        img.alpha_composite(icon, xy)


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _shrink(draw: ImageDraw.ImageDraw, text: str, size: int, weight: str, width: int,
            smallest: int = 15) -> tuple[str, ImageFont.FreeTypeFont]:
    """Text and a font that fits `width`: smaller sizes first (down to `smallest`), cut with … only after that."""
    for s in range(size, smallest - 1, -1):
        if draw.textlength(text, font=_font(s, weight)) <= width:
            return text, _font(s, weight)
    return _fit(draw, text, _font(smallest, weight), width), _font(smallest, weight)


def _card(accent: tuple[int, int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (W, H), (*BACKGROUND, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((PAD // 2, PAD // 2, W - PAD // 2, H - PAD // 2), 28, fill=CARD)
    d.rounded_rectangle((PAD // 2, PAD // 2, PAD // 2 + 10, H - PAD // 2), 5, fill=accent)
    return img, d


def _header(img: Image.Image, d: ImageDraw.ImageDraw, emoji: str, title: str, subtitle: str,
            accent: tuple[int, int, int]) -> None:
    x0, y = PAD + 20, PAD + 14
    _paste_emoji(img, emoji, 58, (x0, y + 4))
    d.text((x0 + 76, y), title.upper(), font=_font(46, "Bold"), fill=accent)
    d.text((x0 + 78, y + 62), subtitle, font=_font(24), fill=MUTED)


def _headings(d: ImageDraw.ImageDraw, y: int, columns: list[tuple[int, str]]) -> None:
    for x, label in columns:
        d.text((x, y + 16), label.upper(), font=_font(22, "Bold"), fill=FAINT)


def _footer(d: ImageDraw.ImageDraw, text: str) -> None:
    """The small grey line at the bottom of every card."""
    d.text((PAD + 20, H - PAD - FOOT + 22), text, font=_font(22), fill=FAINT)


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def _runs(d: ImageDraw.ImageDraw, xy: tuple[int, int], runs: list[tuple[str, ImageFont.FreeTypeFont, tuple]]) -> int:
    """Draw differently styled pieces of text on one line (anchored left-middle). Returns the end x."""
    x, y = xy
    for text, font, colour in runs:
        d.text((x, y), text, font=font, fill=colour, anchor="lm")
        x += int(d.textlength(text, font=font))
    return x


BOARD_ROWS = 6  # most rows a board holds; longer skill groups are split over several boards
BOARD_GAP = 28  # between two columns of a card


def render_board(board: Board) -> bytes:
    rows = board.rows[:BOARD_ROWS]
    # Fewer rows spread out to fill the card instead of leaving a gap at the bottom.
    row_h = min(ROW * BOARD_ROWS // len(rows), 124) if rows else ROW
    img, d = _card(board.accent)
    _header(img, d, board.emoji, board.title, board.subtitle, board.accent)

    x0 = PAD + 20
    label_w = 380
    cell_w = (W - x0 - PAD - label_w) // 3
    y = PAD + HEAD
    d.text((x0 + 8, y + 16), board.first_column.upper(), font=_font(22, "Bold"), fill=FAINT)
    for i, (medal, label, colour) in enumerate(MEDALS):
        cx = x0 + label_w + i * cell_w
        _paste_emoji(img, medal, 40, (cx, y + 8))
        d.text((cx + 50, y + 16), label, font=_font(22, "Bold"), fill=colour)
    y += COLH

    for r, row in enumerate(rows):
        if r % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + row_h - 6), 14, fill=ROW_SHADE)
        mid = y + (row_h - 6) // 2
        _paste_emoji(img, row.emoji, 44, (x0 + 8, mid - 22))
        d.text((x0 + 66, mid), _fit(d, row.label, _font(32, "SemiBold"), label_w - 80),
               font=_font(32, "SemiBold"), fill=TEXT, anchor="lm")
        for i in range(3):
            cx = x0 + label_w + i * cell_w
            if i < len(row.cells):
                name, value = row.cells[i]
                shown, font = _shrink(d, name, 26, "Medium", cell_w - 20)
                d.text((cx, mid - 4), shown, font=font, fill=NAME, anchor="ls")
                d.text((cx, mid + 4), value, font=_font(28, "Bold"), fill=MEDALS[i][2], anchor="lt")
            else:
                d.text((cx, mid), "—", font=_font(26, "Medium"), fill=FAINT, anchor="lm")
        y += row_h

    _footer(d, board.footer)
    return _png(img)


# ---------- clan feed cards ----------

GREEN = (34, 197, 94)
GOLD = (251, 191, 36)
FEED_ROW = 76
MAX_FEED_ROWS = (BODY - COLH) // FEED_ROW  # rows that fit on one card (7); more go in another post


@dataclass
class LevelUp:
    player: str
    skill_emoji: str
    skill: str
    old: int
    new: int
    milestone: bool


def render_level_ups(items: list[LevelUp], footer: str, subtitle: str = "New levels from linked players") -> bytes:
    """One row per skill levelled; rows for the same player share a shaded band. Callers keep it to
    MAX_FEED_ROWS rows (the clan feed starts another post for the rest)."""
    shown, hidden = items[:MAX_FEED_ROWS], len(items) - MAX_FEED_ROWS
    img, d = _card(GREEN)
    _header(img, d, "📈", "Level up!", subtitle, GREEN)
    x0 = PAD + 20
    player_x, skill_x, level_x = x0 + 8, x0 + 440, x0 + 860
    y = PAD + HEAD
    _headings(d, y, [(player_x, "Player"), (skill_x, "Skill"), (level_x, "Level")])
    y += COLH

    band, previous = -1, None
    for item in shown:
        if item.player != previous:
            band += 1
        if band % 2 == 0:
            d.rectangle((x0 - 6, y, W - PAD - 6, y + FEED_ROW), fill=ROW_SHADE)
        mid = y + FEED_ROW // 2
        if item.player != previous:
            name, font = _shrink(d, item.player, 30, "SemiBold", skill_x - player_x - 24, 22)
            d.text((player_x, mid), name, font=font, fill=TEXT, anchor="lm")
        _paste_emoji(img, item.skill_emoji, 42, (skill_x, mid - 21))
        d.text((skill_x + 56, mid), item.skill, font=_font(30, "Medium"), fill=NAME, anchor="lm")
        runs = [(f"{item.old}", _font(28, "Medium"), FAINT), ("  →  ", _font(28), MUTED),
                (f"{item.new}", _font(34, "Bold"), GOLD if item.milestone else GREEN)]
        if item.new - item.old > 1:
            runs.append((f"  +{item.new - item.old}", _font(24, "SemiBold"), MUTED))
        end = _runs(d, (level_x, mid), runs)
        if item.milestone:
            pill_w = 200
            pill_x = max(end + 16, W - PAD - 18 - pill_w)  # right-aligned in the row
            d.rounded_rectangle((pill_x, mid - 21, pill_x + pill_w, mid + 21), 21, fill=(66, 52, 18))
            _paste_emoji(img, "🎉", 28, (pill_x + 12, mid - 14))
            d.text((pill_x + 48, mid), "MILESTONE", font=_font(20, "Bold"), fill=GOLD, anchor="lm")
        previous = item.player
        y += FEED_ROW
    _footer(d, footer + (f" · …and {hidden} more" if hidden > 0 else ""))
    return _png(img)


@dataclass
class Gainer:
    player: str
    xp: str  # e.g. "+1.2M"
    levels: int
    skill_emoji: str
    skill: str
    skill_xp: str  # e.g. "+800K"


def render_top_gainers(rows: list[Gainer], title: str, subtitle: str, footer: str) -> bytes:
    img, d = _card(GOLD)
    _header(img, d, "🏆", title, subtitle, GOLD)
    x0 = PAD + 20
    rank_x, player_x, xp_x, levels_x, skill_x = x0 + 8, x0 + 100, x0 + 560, x0 + 850, x0 + 1050
    y = PAD + HEAD
    _headings(d, y, [(player_x, "Player"), (xp_x, "XP gained"), (levels_x, "Levels"), (skill_x, "Top skill")])
    y += COLH

    for i, row in enumerate(rows[:(BODY - COLH) // ROW]):
        if i % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + ROW - 6), 14, fill=ROW_SHADE)
        mid = y + (ROW - 6) // 2
        if i < len(MEDALS):
            _paste_emoji(img, MEDALS[i][0], 46, (rank_x, mid - 23))
        else:
            d.text((rank_x + 10, mid), f"#{i + 1}", font=_font(30, "Bold"), fill=FAINT, anchor="lm")
        colour = MEDALS[i][2] if i < len(MEDALS) else NAME
        d.text((player_x, mid), _fit(d, row.player, _font(30, "SemiBold"), xp_x - player_x - 24),
               font=_font(30, "SemiBold"), fill=TEXT, anchor="lm")
        d.text((xp_x, mid), row.xp, font=_font(32, "Bold"), fill=colour, anchor="lm")
        d.text((levels_x, mid), str(row.levels) if row.levels else "—", font=_font(30, "Medium"),
               fill=NAME if row.levels else FAINT, anchor="lm")
        _paste_emoji(img, row.skill_emoji, 40, (skill_x, mid - 20))
        _runs(d, (skill_x + 52, mid), [
            (_fit(d, row.skill, _font(26, "Medium"), 200), _font(26, "Medium"), NAME),
            (f"  {row.skill_xp}", _font(24, "SemiBold"), MUTED),
        ])
        y += ROW

    _footer(d, footer)
    return _png(img)


# ---------- clan cards ----------

BLUE = (96, 165, 250)
TILE_H = 120
MAX_BANK_ITEMS = 8


@dataclass
class Contributor:
    player: str
    xp: str  # clan XP, e.g. "12.4K"
    quests: str
    boss: str
    firepit: str


def render_clan_week(title: str, subtitle: str, stats: list[tuple[str, str, str]], rows: list[Contributor],
                     footer: str) -> bytes:
    """Clan totals as a row of tiles (emoji, label, value), then the top contributors."""
    img, d = _card(BLUE)
    _header(img, d, "🏰", title, subtitle, BLUE)
    x0 = PAD + 20
    y = _tiles(img, d, PAD + HEAD, stats, per_row=6)

    row_h = 84
    rank_x, player_x, xp_x, quests_x, boss_x, fire_x = x0 + 8, x0 + 100, x0 + 560, x0 + 830, x0 + 1040, x0 + 1300
    _headings(d, y, [(player_x, "Top contributors"), (xp_x, "Clan XP"), (quests_x, "Quests"),
                     (boss_x, "Boss dmg"), (fire_x, "Firepit")])
    y += COLH
    for i, row in enumerate(rows[:5]):
        if i % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + row_h - 6), 14, fill=ROW_SHADE)
        mid = y + (row_h - 6) // 2
        if i < len(MEDALS):
            _paste_emoji(img, MEDALS[i][0], 42, (rank_x, mid - 21))
        else:
            d.text((rank_x + 10, mid), f"#{i + 1}", font=_font(28, "Bold"), fill=FAINT, anchor="lm")
        d.text((player_x, mid), _fit(d, row.player, _font(30, "SemiBold"), xp_x - player_x - 24),
               font=_font(30, "SemiBold"), fill=TEXT, anchor="lm")
        d.text((xp_x, mid), row.xp, font=_font(32, "Bold"), fill=MEDALS[i][2] if i < len(MEDALS) else NAME, anchor="lm")
        for x, text in ((quests_x, row.quests), (boss_x, row.boss), (fire_x, row.firepit)):
            d.text((x, mid), text, font=_font(28, "Medium"), fill=NAME if text != "—" else FAINT, anchor="lm")
        y += row_h
    if not rows:
        d.text((player_x, y + row_h // 2), "No contributions this week", font=_font(26, "Medium"), fill=MUTED, anchor="lm")

    _footer(d, footer)
    return _png(img)


RARITY_COLOURS = {
    "common": (161, 161, 170), "uncommon": (34, 197, 94), "rare": (59, 130, 246),
    "epic": (168, 85, 247), "legendary": (251, 146, 60), "mythic": (239, 68, 68),
}


@dataclass
class BankItem:
    name: str
    quantity: str
    value: str
    rarity: str


def _tiles(img: Image.Image, d: ImageDraw.ImageDraw, y: int, stats: list[tuple[str, str, str]],
           per_row: int = 3, left: int = PAD + 20, right: int = W - PAD - 6) -> int:
    """Stat tiles (emoji, label, value), `per_row` across between `left` and `right`. Returns the y below them."""
    gap = 16
    tile_w = (right - left + 6 - (per_row - 1) * gap) // per_row
    label_font = _font(20 if tile_w > 240 else 17, "Bold")
    for i, (emoji, label, value) in enumerate(stats):
        tx = left - 6 + (i % per_row) * (tile_w + gap)
        ty = y + (i // per_row) * (TILE_H + gap)
        d.rounded_rectangle((tx, ty, tx + tile_w, ty + TILE_H), 16, fill=ROW_SHADE)
        _paste_emoji(img, emoji, 30, (tx + 20, ty + 18))
        d.text((tx + 60, ty + 33), _fit(d, label.upper(), label_font, tile_w - 72), font=label_font, fill=FAINT,
               anchor="lm")
        d.text((tx + 20, ty + 82), _fit(d, value, _font(38, "Bold"), tile_w - 40), font=_font(38, "Bold"),
               fill=TEXT, anchor="lm")
    return y + -(-len(stats) // per_row) * (TILE_H + gap) + gap


def render_bank(title: str, subtitle: str, stats: list[tuple[str, str, str]], items: list[BankItem],
                footer: str) -> bytes:
    """The clan bank summary: stat tiles on the left, the most valuable items (with a rarity dot) on the right."""
    img, d = _card(BLUE)
    _header(img, d, "🏦", title, subtitle, BLUE)
    x0 = PAD + 20
    split = x0 + 560
    _tiles(img, d, PAD + HEAD, stats, per_row=2, right=split - 24)

    item_row = 72
    rank_x, name_x, qty_x, value_x = split + 8, split + 60, split + 520, split + 730
    y = PAD + HEAD
    _headings(d, y, [(name_x, "Top items"), (qty_x, "Quantity"), (value_x, "Value")])
    y += COLH
    for i, item in enumerate(items[:MAX_BANK_ITEMS]):
        if i % 2 == 0:
            d.rounded_rectangle((split - 6, y, W - PAD - 6, y + item_row - 6), 14, fill=ROW_SHADE)
        mid = y + (item_row - 6) // 2
        d.text((rank_x + 6, mid), f"{i + 1}", font=_font(26, "Bold"), fill=FAINT, anchor="lm")
        colour = RARITY_COLOURS.get(item.rarity.lower(), MUTED)
        d.ellipse((name_x, mid - 8, name_x + 16, mid + 8), fill=colour)
        d.text((name_x + 30, mid), _fit(d, item.name, _font(28, "SemiBold"), qty_x - name_x - 50),
               font=_font(28, "SemiBold"), fill=TEXT, anchor="lm")
        d.text((qty_x, mid), item.quantity, font=_font(28, "Medium"), fill=NAME, anchor="lm")
        d.text((value_x, mid), item.value, font=_font(30, "Bold"), fill=GOLD, anchor="lm")
        y += item_row
    if not items:
        d.text((name_x, y + item_row // 2), "The bank is empty", font=_font(26, "Medium"), fill=MUTED, anchor="lm")

    _footer(d, footer)
    return _png(img)


# ---------- clan bank activity and clan boss ----------

RED = (239, 68, 68)
SHIELD = (56, 189, 248)
BAR_BACK = (48, 41, 66)


def _bar(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fraction: float, colour: tuple) -> None:
    x0, y0, x1, y1 = box
    radius = (y1 - y0) // 2
    d.rounded_rectangle(box, radius, fill=BAR_BACK)
    fill = round((x1 - x0) * max(0.0, min(1.0, fraction)))
    if fill > 0:
        d.rounded_rectangle((x0, y0, x0 + max(fill, 2 * radius), y1), radius, fill=colour)


def _pill(d: ImageDraw.ImageDraw, right: int, mid: int, text: str, colour: tuple, back: tuple) -> int:
    """A rounded label ending at x=`right`. Returns its left edge."""
    font = _font(22, "Bold")
    w = int(d.textlength(text, font=font)) + 36
    d.rounded_rectangle((right - w, mid - 22, right, mid + 22), 22, fill=back)
    d.text((right - w // 2, mid), text, font=font, fill=colour, anchor="mm")
    return right - w


@dataclass
class Activity:
    kind: str  # "in", "out", "request" or anything else
    player: str
    what: str  # e.g. "50 × Bones"
    value: str
    when: str


MAX_ACTIVITY_ROWS = (BODY - COLH) // 72  # 8

ACTIVITY_STYLE = {
    "in": ("📥", "DEPOSIT", GREEN, (20, 60, 38)),
    "out": ("📤", "WITHDRAW", RED, (70, 26, 30)),
    "request": ("🙋", "REQUEST", GOLD, (66, 52, 18)),
}


def render_bank_activity(title: str, subtitle: str, rows: list[Activity], footer: str) -> bytes:
    row_h = 72
    img, d = _card(BLUE)
    _header(img, d, "📜", title, subtitle, BLUE)
    x0 = PAD + 20
    icon_x, player_x, kind_x, what_x, value_x, when_x = x0 + 4, x0 + 70, x0 + 400, x0 + 610, x0 + 1080, x0 + 1260
    y = PAD + HEAD
    _headings(d, y, [(player_x, "Player"), (kind_x, "Action"), (what_x, "Item"), (value_x, "Value"),
                     (when_x, "When (UTC)")])
    y += COLH
    for i, row in enumerate(rows[:MAX_ACTIVITY_ROWS]):
        if i % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + row_h - 6), 14, fill=ROW_SHADE)
        mid = y + (row_h - 6) // 2
        emoji, label, colour, back = ACTIVITY_STYLE.get(row.kind, ("🔁", row.kind.upper()[:10] or "?", MUTED, ROW_SHADE))
        _paste_emoji(img, emoji, 36, (icon_x, mid - 18))
        d.text((player_x, mid), _fit(d, row.player, _font(26, "SemiBold"), kind_x - player_x - 16),
               font=_font(26, "SemiBold"), fill=TEXT, anchor="lm")
        pill_w = int(d.textlength(label, font=_font(18, "Bold"))) + 28
        d.rounded_rectangle((kind_x, mid - 18, kind_x + pill_w, mid + 18), 18, fill=back)
        d.text((kind_x + pill_w // 2, mid), label, font=_font(18, "Bold"), fill=colour, anchor="mm")
        d.text((what_x, mid), _fit(d, row.what, _font(26, "Medium"), value_x - what_x - 16),
               font=_font(26, "Medium"), fill=NAME, anchor="lm")
        d.text((value_x, mid), row.value, font=_font(26, "Bold"), fill=GOLD if row.value != "—" else FAINT, anchor="lm")
        d.text((when_x, mid), row.when, font=_font(22, "Medium"), fill=MUTED, anchor="lm")
        y += row_h
    if not rows:
        d.text((player_x, y + row_h // 2), "Nothing deposited or withdrawn yet", font=_font(26, "Medium"),
               fill=MUTED, anchor="lm")
    _footer(d, footer)
    return _png(img)


@dataclass
class BossPart:
    emoji: str
    name: str
    hp: float
    max_hp: float
    shield: float
    shield_max: float


@dataclass
class BossCard:
    subtitle: str
    tier: str
    max_tier: str
    alive: bool
    status: str
    hp: float
    max_hp: float
    parts: list[BossPart]
    footer: list[str]  # lines


def _short(value: float) -> str:
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f"{value / size:.1f}{suffix}"
    return f"{value:,.0f}"


def render_boss(card: BossCard) -> bytes:
    part_h = 92
    accent = RED if card.alive else MUTED
    img, d = _card(accent)
    _header(img, d, "⚔️" if card.alive else "💀", "Clan boss", card.subtitle, accent)
    x0, right = PAD + 20, W - PAD - 12

    # Tier and status, top right
    mid = PAD + 50
    left = _pill(d, right, mid, card.status.upper(), GREEN if card.alive else RED,
                 (20, 60, 38) if card.alive else (70, 26, 30))
    _pill(d, left - 12, mid, f"TIER {card.tier} / {card.max_tier}", GOLD, (66, 52, 18))

    # Overall HP
    y = PAD + HEAD
    pct = card.hp / card.max_hp if card.max_hp else 0
    d.text((x0, y + 16), "HEALTH", font=_font(22, "Bold"), fill=FAINT, anchor="lm")
    d.text((right, y + 16), f"{_short(card.hp)} / {_short(card.max_hp)}", font=_font(26, "Medium"),
           fill=NAME, anchor="rm")
    _bar(d, (x0, y + 40, right, y + 96), pct, RED if card.alive else MUTED)
    d.text(((x0 + right) // 2, y + 68), f"{pct:.1%}".replace(".0%", "%"), font=_font(34, "Bold"), fill=TEXT, anchor="mm")
    y += 116

    # Parts
    name_x, bar_x, text_w = x0 + 8, x0 + 300, 260
    bar_end = right - text_w
    _headings(d, y - 8, [(name_x, "Part"), (bar_x, "Health / shield")])
    y += COLH - 12
    for i, part in enumerate(card.parts[:4]):
        if i % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + part_h - 6), 14, fill=ROW_SHADE)
        mid = y + (part_h - 6) // 2
        _paste_emoji(img, part.emoji, 42, (name_x, mid - 21))
        d.text((name_x + 60, mid), part.name, font=_font(30, "SemiBold"), fill=TEXT, anchor="lm")
        hp_frac = part.hp / part.max_hp if part.max_hp else 0
        _bar(d, (bar_x, mid - 24, bar_end, mid - 4), hp_frac, RED if part.hp > 0 else MUTED)
        d.text((bar_end + 20, mid - 14), f"{_short(part.hp)} / {_short(part.max_hp)}", font=_font(24, "SemiBold"),
               fill=NAME, anchor="lm")
        if part.shield_max:
            _bar(d, (bar_x, mid + 6, bar_end, mid + 22), part.shield / part.shield_max, SHIELD)
            text, colour = f"{_short(part.shield)} / {_short(part.shield_max)}", SHIELD
        else:
            _bar(d, (bar_x, mid + 6, bar_end, mid + 22), 0, SHIELD)
            text, colour = "No shield", FAINT
        d.text((bar_end + 20, mid + 15), text, font=_font(22, "Medium"), fill=colour, anchor="lm")
        y += part_h

    _footer(d, " · ".join(card.footer))
    return _png(img)


@dataclass
class DamageRow:
    player: str
    damage: float
    share: float  # 0-1 of the damage everyone listed has done to this boss


MAX_DAMAGE_ROWS = 5


def render_boss_damage(title: str, subtitle: str, rows: list[DamageRow], footer: str, alive: bool) -> bytes:
    """Who has hit the current clan boss hardest: damage and share of the total, best first."""
    shown = rows[:MAX_DAMAGE_ROWS]
    accent = RED if alive else MUTED
    img, d = _card(accent)
    _header(img, d, "💥", title, subtitle, accent)
    x0, right = PAD + 20, W - PAD - 12
    rank_x, player_x, dmg_x, bar_x = x0 + 8, x0 + 100, x0 + 560, x0 + 840
    y = PAD + HEAD
    _headings(d, y, [(player_x, "Player"), (dmg_x, "Damage"), (bar_x, "Share")])
    y += COLH

    if not shown:
        d.text((player_x, y + (ROW - 6) // 2), "No damage yet", font=_font(30, "Medium"), fill=FAINT, anchor="lm")
    for i, row in enumerate(shown):
        if i % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + ROW - 6), 14, fill=ROW_SHADE)
        mid = y + (ROW - 6) // 2
        if i < len(MEDALS):
            _paste_emoji(img, MEDALS[i][0], 46, (rank_x, mid - 23))
        else:
            d.text((rank_x + 10, mid), f"#{i + 1}", font=_font(30, "Bold"), fill=FAINT, anchor="lm")
        colour = MEDALS[i][2] if i < len(MEDALS) else NAME
        name, font = _shrink(d, row.player, 30, "SemiBold", dmg_x - player_x - 24, 22)
        d.text((player_x, mid), name, font=font, fill=TEXT, anchor="lm")
        d.text((dmg_x, mid), _short(row.damage), font=_font(32, "Bold"), fill=colour, anchor="lm")
        pct_w = 100
        _bar(d, (bar_x, mid - 12, right - pct_w, mid + 12), row.share, RED if alive else MUTED)
        share = "<1%" if row.share < 0.01 else ">99%" if 0.99 < row.share < 1 and f"{row.share:.0%}" == "100%" \
            else f"{row.share:.0%}"  # only a real 100% shows as 100%
        d.text((right, mid), share, font=_font(26, "SemiBold"), fill=NAME, anchor="rm")
        y += ROW

    _footer(d, footer)
    return _png(img)


# ---------- one-off clan feed announcements ----------

PURPLE = (168, 85, 247)


@dataclass
class Announcement:
    emoji: str
    accent: tuple[int, int, int]
    title: str
    subtitle: str
    headline: str  # big line, e.g. "Level 1  →  2"
    detail: str  # smaller line under it
    stats: list[tuple[str, str, str]]  # tiles: (emoji, label, value)
    progress: tuple[float, str, str] | None  # (fraction, left label, right label)
    footer: str


def render_announcement(a: Announcement) -> bytes:
    img, d = _card(a.accent)
    _header(img, d, a.emoji, a.title, a.subtitle, a.accent)
    x0, right = PAD + 20, W - PAD - 12

    y = PAD + HEAD
    d.rounded_rectangle((x0 - 6, y, right + 6, y + 120), 16, fill=ROW_SHADE)
    d.text((x0 + 18, y + 44), _fit(d, a.headline, _font(52, "Bold"), right - x0 - 40), font=_font(52, "Bold"),
           fill=a.accent, anchor="lm")
    d.text((x0 + 20, y + 94), _fit(d, a.detail, _font(26, "Medium"), right - x0 - 40), font=_font(26, "Medium"),
           fill=NAME, anchor="lm")
    y += 150

    if a.progress:
        fraction, left, right_label = a.progress
        d.text((x0, y + 16), left.upper(), font=_font(22, "Bold"), fill=FAINT, anchor="lm")
        d.text((right, y + 16), right_label, font=_font(24, "Medium"), fill=NAME, anchor="rm")
        _bar(d, (x0, y + 40, right, y + 84), fraction, a.accent)
        y += 124

    if a.stats:
        _tiles(img, d, y, a.stats, per_row=min(len(a.stats), 4))
    _footer(d, a.footer)
    return _png(img)


