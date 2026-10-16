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

W, PAD, HEAD, COLH, ROW, FOOT = 1200, 36, 130, 64, 96, 70
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


def _card(height: int, accent: tuple[int, int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (W, height), (*BACKGROUND, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((PAD // 2, PAD // 2, W - PAD // 2, height - PAD // 2), 28, fill=CARD)
    d.rounded_rectangle((PAD // 2, PAD // 2, PAD // 2 + 10, height - PAD // 2), 5, fill=accent)
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


def render_board(board: Board) -> bytes:
    height = PAD + HEAD + COLH + ROW * len(board.rows) + FOOT + PAD
    img, d = _card(height, board.accent)
    _header(img, d, board.emoji, board.title, board.subtitle, board.accent)
    x0 = PAD + 20

    # Column headings
    y = PAD + HEAD
    col_w = (W - x0 - PAD - LABEL_W) // 3
    d.text((x0 + 8, y + 16), board.first_column.upper(), font=_font(22, "Bold"), fill=FAINT)
    for i, (medal, label, colour) in enumerate(MEDALS):
        cx = x0 + LABEL_W + i * col_w
        _paste_emoji(img, medal, 40, (cx, y + 8))
        d.text((cx + 50, y + 16), label, font=_font(22, "Bold"), fill=colour)

    # Rows
    y += COLH
    name_font, value_font = _font(26, "Medium"), _font(28, "Bold")
    for r, row in enumerate(board.rows):
        if r % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + ROW - 6), 14, fill=ROW_SHADE)
        _paste_emoji(img, row.emoji, 44, (x0 + 8, y + (ROW - 50) // 2))
        d.text((x0 + 68, y + ROW // 2 - 3), _fit(d, row.label, _font(32, "SemiBold"), LABEL_W - 80),
               font=_font(32, "SemiBold"), fill=TEXT, anchor="lm")
        for i in range(3):
            cx = x0 + LABEL_W + i * col_w
            if i < len(row.cells):
                name, value = row.cells[i]
                d.text((cx, y + 14), _fit(d, name, name_font, col_w - 20), font=name_font, fill=NAME)
                d.text((cx, y + 48), value, font=value_font, fill=MEDALS[i][2])
            else:
                d.text((cx, y + ROW // 2 - 3), "—", font=name_font, fill=FAINT, anchor="lm")
        y += ROW

    d.text((x0, y + 22), board.footer, font=_font(22), fill=FAINT)
    return _png(img)


# ---------- clan feed cards ----------

GREEN = (34, 197, 94)
GOLD = (251, 191, 36)
FEED_ROW = 76
MAX_FEED_ROWS = 20


@dataclass
class LevelUp:
    player: str
    skill_emoji: str
    skill: str
    old: int
    new: int
    milestone: bool


def render_level_ups(items: list[LevelUp], footer: str) -> bytes:
    """One row per skill levelled; rows for the same player share a shaded band."""
    shown, hidden = items[:MAX_FEED_ROWS], len(items) - MAX_FEED_ROWS
    height = PAD + HEAD + COLH + FEED_ROW * len(shown) + (FEED_ROW // 2 if hidden > 0 else 0) + FOOT + PAD
    img, d = _card(height, GREEN)
    _header(img, d, "📈", "Level up!", "New levels from linked clan members", GREEN)
    x0 = PAD + 20
    player_x, skill_x, level_x = x0 + 8, x0 + 300, x0 + 600
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
            d.text((player_x, mid), _fit(d, item.player, _font(30, "SemiBold"), skill_x - player_x - 24),
                   font=_font(30, "SemiBold"), fill=TEXT, anchor="lm")
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
    if hidden > 0:
        d.text((player_x, y + FEED_ROW // 4), f"…and {hidden} more", font=_font(24, "Medium"), fill=MUTED, anchor="lm")
        y += FEED_ROW // 2

    d.text((x0, y + 22), footer, font=_font(22), fill=FAINT)
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
    height = PAD + HEAD + COLH + ROW * len(rows) + FOOT + PAD
    img, d = _card(height, GOLD)
    _header(img, d, "🏆", title, subtitle, GOLD)
    x0 = PAD + 20
    rank_x, player_x, xp_x, levels_x, skill_x = x0 + 8, x0 + 90, x0 + 420, x0 + 640, x0 + 790
    y = PAD + HEAD
    _headings(d, y, [(player_x, "Player"), (xp_x, "XP gained"), (levels_x, "Levels"), (skill_x, "Top skill")])
    y += COLH

    for i, row in enumerate(rows):
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
            (_fit(d, row.skill, _font(26, "Medium"), 170), _font(26, "Medium"), NAME),
            (f"  {row.skill_xp}", _font(24, "SemiBold"), MUTED),
        ])
        y += ROW

    d.text((x0, y + 22), footer, font=_font(22), fill=FAINT)
    return _png(img)


# ---------- clan cards ----------

BLUE = (96, 165, 250)
TILE_H = 120


@dataclass
class Contributor:
    player: str
    xp: str  # clan XP, e.g. "12.4K"
    quests: str
    boss: str
    firepit: str


def render_clan_week(title: str, subtitle: str, stats: list[tuple[str, str, str]], rows: list[Contributor],
                     footer: str) -> bytes:
    """Clan totals as a strip of tiles (emoji, label, value), then the top contributors."""
    tile_rows = (len(stats) + 2) // 3
    height = PAD + HEAD + tile_rows * (TILE_H + 16) + 16 + COLH + ROW * max(len(rows), 1) + FOOT + PAD
    img, d = _card(height, BLUE)
    _header(img, d, "🏰", title, subtitle, BLUE)
    x0 = PAD + 20

    y = _tiles(img, d, PAD + HEAD, stats)

    rank_x, player_x, xp_x, quests_x, boss_x, fire_x = x0 + 8, x0 + 90, x0 + 420, x0 + 610, x0 + 770, x0 + 960
    _headings(d, y, [(player_x, "Top contributors"), (xp_x, "Clan XP"), (quests_x, "Quests"),
                     (boss_x, "Boss dmg"), (fire_x, "Firepit")])
    y += COLH
    for i, row in enumerate(rows):
        if i % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + ROW - 6), 14, fill=ROW_SHADE)
        mid = y + (ROW - 6) // 2
        if i < len(MEDALS):
            _paste_emoji(img, MEDALS[i][0], 46, (rank_x, mid - 23))
        else:
            d.text((rank_x + 10, mid), f"#{i + 1}", font=_font(30, "Bold"), fill=FAINT, anchor="lm")
        d.text((player_x, mid), _fit(d, row.player, _font(30, "SemiBold"), xp_x - player_x - 24),
               font=_font(30, "SemiBold"), fill=TEXT, anchor="lm")
        d.text((xp_x, mid), row.xp, font=_font(32, "Bold"), fill=MEDALS[i][2] if i < len(MEDALS) else NAME, anchor="lm")
        for x, text in ((quests_x, row.quests), (boss_x, row.boss), (fire_x, row.firepit)):
            d.text((x, mid), text, font=_font(28, "Medium"), fill=NAME if text != "—" else FAINT, anchor="lm")
        y += ROW
    if not rows:
        d.text((player_x, y + ROW // 2), "No contributions this week", font=_font(26, "Medium"), fill=MUTED, anchor="lm")
        y += ROW

    d.text((x0, y + 22), footer, font=_font(22), fill=FAINT)
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


def _tiles(img: Image.Image, d: ImageDraw.ImageDraw, y: int, stats: list[tuple[str, str, str]]) -> int:
    """Stat tiles, three per row (emoji, label, value). Returns the y below them."""
    x0 = PAD + 20
    tile_w = (W - x0 - PAD - 6 - 2 * 16) // 3
    for i, (emoji, label, value) in enumerate(stats):
        tx = x0 - 6 + (i % 3) * (tile_w + 16)
        ty = y + (i // 3) * (TILE_H + 16)
        d.rounded_rectangle((tx, ty, tx + tile_w, ty + TILE_H), 16, fill=ROW_SHADE)
        _paste_emoji(img, emoji, 30, (tx + 20, ty + 18))
        d.text((tx + 60, ty + 33), label.upper(), font=_font(20, "Bold"), fill=FAINT, anchor="lm")
        d.text((tx + 20, ty + 82), _fit(d, value, _font(38, "Bold"), tile_w - 40), font=_font(38, "Bold"),
               fill=TEXT, anchor="lm")
    return y + (len(stats) + 2) // 3 * (TILE_H + 16) + 16


def render_bank(title: str, subtitle: str, stats: list[tuple[str, str, str]], items: list[BankItem],
                footer: str) -> bytes:
    """The clan bank summary: stat tiles, then the most valuable items with a rarity dot."""
    item_row = 72
    height = (PAD + HEAD + (len(stats) + 2) // 3 * (TILE_H + 16) + 16 + COLH
              + item_row * max(len(items), 1) + FOOT + PAD)
    img, d = _card(height, BLUE)
    _header(img, d, "🏦", title, subtitle, BLUE)
    x0 = PAD + 20
    y = _tiles(img, d, PAD + HEAD, stats)

    rank_x, name_x, qty_x, value_x = x0 + 8, x0 + 80, x0 + 640, x0 + 880
    _headings(d, y, [(name_x, "Top items"), (qty_x, "Quantity"), (value_x, "Value")])
    y += COLH
    for i, item in enumerate(items):
        if i % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + item_row - 6), 14, fill=ROW_SHADE)
        mid = y + (item_row - 6) // 2
        d.text((rank_x + 6, mid), f"{i + 1}", font=_font(26, "Bold"), fill=FAINT, anchor="lm")
        colour = RARITY_COLOURS.get(item.rarity.lower(), MUTED)
        d.ellipse((name_x, mid - 8, name_x + 16, mid + 8), fill=colour)
        d.text((name_x + 30, mid), _fit(d, item.name, _font(28, "SemiBold"), qty_x - name_x - 60),
               font=_font(28, "SemiBold"), fill=TEXT, anchor="lm")
        d.text((qty_x, mid), item.quantity, font=_font(28, "Medium"), fill=NAME, anchor="lm")
        d.text((value_x, mid), item.value, font=_font(30, "Bold"), fill=GOLD, anchor="lm")
        y += item_row
    if not items:
        d.text((name_x, y + item_row // 2), "The bank is empty", font=_font(26, "Medium"), fill=MUTED, anchor="lm")
        y += item_row

    d.text((x0, y + 22), footer, font=_font(22), fill=FAINT)
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


ACTIVITY_STYLE = {
    "in": ("📥", "DEPOSIT", GREEN, (20, 60, 38)),
    "out": ("📤", "WITHDRAW", RED, (70, 26, 30)),
    "request": ("🙋", "REQUEST", GOLD, (66, 52, 18)),
}


def render_bank_activity(title: str, subtitle: str, rows: list[Activity], footer: str) -> bytes:
    row_h = 70
    height = PAD + HEAD + COLH + row_h * max(len(rows), 1) + FOOT + PAD
    img, d = _card(height, BLUE)
    _header(img, d, "📜", title, subtitle, BLUE)
    x0 = PAD + 20
    icon_x, player_x, kind_x, what_x, value_x, when_x = x0 + 4, x0 + 64, x0 + 290, x0 + 470, x0 + 800, x0 + 940
    y = PAD + HEAD
    _headings(d, y, [(player_x, "Player"), (kind_x, "Action"), (what_x, "Item"), (value_x, "Value"),
                     (when_x, "When (UTC)")])
    y += COLH
    for i, row in enumerate(rows):
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
        y += row_h
    d.text((x0, y + 22), footer, font=_font(22), fill=FAINT)
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
    part_h = 116
    height = PAD + HEAD + 150 + COLH + part_h * len(card.parts) + 40 * len(card.footer) + 30 + PAD
    accent = RED if card.alive else MUTED
    img, d = _card(height, accent)
    _header(img, d, "⚔️" if card.alive else "💀", "Clan boss", card.subtitle, accent)
    x0, right = PAD + 20, W - PAD - 12

    # Tier and status, top right
    mid = PAD + 50
    left = _pill(d, right, mid, card.status.upper(), GREEN if card.alive else RED,
                 (20, 60, 38) if card.alive else (70, 26, 30))
    _pill(d, left - 12, mid, f"TIER {card.tier} / {card.max_tier}", GOLD, (66, 52, 18))

    # Overall HP
    y = PAD + HEAD + 6
    pct = card.hp / card.max_hp if card.max_hp else 0
    d.text((x0, y + 18), "HEALTH", font=_font(22, "Bold"), fill=FAINT, anchor="lm")
    d.text((right, y + 18), f"{_short(card.hp)} / {_short(card.max_hp)}", font=_font(26, "Medium"),
           fill=NAME, anchor="rm")
    _bar(d, (x0, y + 44, right, y + 104), pct, RED if card.alive else MUTED)
    d.text(((x0 + right) // 2, y + 74), f"{pct:.1%}".replace(".0%", "%"), font=_font(34, "Bold"), fill=TEXT, anchor="mm")
    y += 150

    # Parts
    name_x, bar_x, text_w = x0 + 8, x0 + 260, 230
    bar_end = right - text_w
    _headings(d, y, [(name_x, "Part"), (bar_x, "Health / shield")])
    y += COLH
    for i, part in enumerate(card.parts):
        if i % 2 == 0:
            d.rounded_rectangle((x0 - 6, y, W - PAD - 6, y + part_h - 8), 14, fill=ROW_SHADE)
        mid = y + (part_h - 8) // 2
        _paste_emoji(img, part.emoji, 44, (name_x, mid - 22))
        d.text((name_x + 60, mid), part.name, font=_font(30, "SemiBold"), fill=TEXT, anchor="lm")
        hp_frac = part.hp / part.max_hp if part.max_hp else 0
        _bar(d, (bar_x, mid - 30, bar_end, mid - 4), hp_frac, RED if part.hp > 0 else MUTED)
        d.text((bar_end + 20, mid - 17), f"{_short(part.hp)} / {_short(part.max_hp)}", font=_font(24, "SemiBold"),
               fill=NAME, anchor="lm")
        if part.shield_max:
            _bar(d, (bar_x, mid + 8, bar_end, mid + 28), part.shield / part.shield_max, SHIELD)
            text, colour = f"🛡 {_short(part.shield)} / {_short(part.shield_max)}", SHIELD
        else:
            _bar(d, (bar_x, mid + 8, bar_end, mid + 28), 0, SHIELD)
            text, colour = "No shield", FAINT
        d.text((bar_end + 20, mid + 18), text.replace("🛡 ", ""), font=_font(22, "Medium"), fill=colour, anchor="lm")
        y += part_h

    y += 14
    for line in card.footer:
        d.text((x0, y + 16), line, font=_font(24, "Medium"), fill=MUTED, anchor="lm")
        y += 40
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
    tiles_h = (len(a.stats) + 2) // 3 * (TILE_H + 16) if a.stats else 0
    height = PAD + HEAD + 130 + (110 if a.progress else 0) + tiles_h + FOOT + PAD
    img, d = _card(height, a.accent)
    _header(img, d, a.emoji, a.title, a.subtitle, a.accent)
    x0, right = PAD + 20, W - PAD - 12

    y = PAD + HEAD
    d.rounded_rectangle((x0 - 6, y, right + 6, y + 112), 16, fill=ROW_SHADE)
    d.text((x0 + 18, y + 40), _fit(d, a.headline, _font(48, "Bold"), right - x0 - 40), font=_font(48, "Bold"),
           fill=a.accent, anchor="lm")
    d.text((x0 + 20, y + 88), _fit(d, a.detail, _font(24, "Medium"), right - x0 - 40), font=_font(24, "Medium"),
           fill=NAME, anchor="lm")
    y += 130

    if a.progress:
        fraction, left, right_label = a.progress
        d.text((x0, y + 16), left.upper(), font=_font(22, "Bold"), fill=FAINT, anchor="lm")
        d.text((right, y + 16), right_label, font=_font(24, "Medium"), fill=NAME, anchor="rm")
        _bar(d, (x0, y + 40, right, y + 80), fraction, a.accent)
        y += 110

    if a.stats:
        y = _tiles(img, d, y, a.stats) - 16
    d.text((x0, y + 30), a.footer, font=_font(22), fill=FAINT)
    return _png(img)
