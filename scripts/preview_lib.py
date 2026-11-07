"""Render a real discord.Embed to a Discord-styled PNG, so feature previews show
the actual embed the bot will post. Inline emoji use the bundled Twemoji where
present; the coloured status squares/circles are drawn when no Twemoji exists.

Runs inside the bot image (Pillow + fonts + discord.py).
"""

from __future__ import annotations

import os
import re
import time

from PIL import Image, ImageDraw, ImageFont

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

PAGE = (30, 31, 34)
EMBED = (43, 45, 49)
WHITE = (242, 243, 245)
TEXT = (219, 222, 225)
MUTED = (148, 155, 164)
LINK = (0, 168, 252)

# Emoji with no bundled Twemoji that we draw as a coloured disc.
DOT_EMOJI = {"🟢": (87, 242, 135), "🟡": (254, 231, 92), "🔴": (237, 66, 69),
             "🟠": (235, 140, 60), "🔵": (88, 101, 242), "🌸": (235, 110, 180)}

W = 1180
PAD = 36
ACCENT_W = 8
LEFT = PAD + ACCENT_W + 28
RIGHT = W - PAD - 28


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(os.path.join(ASSETS, "fonts", "Inter.ttf"), size)
    try:
        f.set_variation_by_name("Bold" if bold else "Regular")
    except Exception:
        pass
    return f


def _twemoji(char: str, size: int) -> Image.Image | None:
    path = os.path.join(ASSETS, "twemoji", f"{ord(char):x}.png")
    if not os.path.exists(path):
        return None
    return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)


def _is_emoji(ch: str) -> bool:
    if ch in DOT_EMOJI:
        return True
    o = ord(ch)
    return o >= 0x2190 and os.path.exists(os.path.join(ASSETS, "twemoji", f"{o:x}.png"))


def _tokens(word: str):
    """Split a word into ('text', str) / ('emoji', ch) runs."""
    out, buf = [], ""
    for ch in word:
        if ch == "️":  # variation selector
            continue
        if _is_emoji(ch):
            if buf:
                out.append(("text", buf)); buf = ""
            out.append(("emoji", ch))
        else:
            buf += ch
    if buf:
        out.append(("text", buf))
    return out


def _word_width(d, word, fnt, bfnt, bold, em):
    f = bfnt if bold else fnt
    w = 0
    for kind, val in _tokens(word):
        w += em if kind == "emoji" else d.textlength(val, font=f)
    return w


def _draw_word(d, img, x, y, word, fnt, bfnt, bold, colour, em, baseline_off):
    f = bfnt if bold else fnt
    for kind, val in _tokens(word):
        if kind == "emoji":
            ix = int(x)
            if val in DOT_EMOJI and _twemoji(val, em) is None:
                r = int(em * 0.42)
                cy = int(y + baseline_off // 2 + em // 2 - 2)
                d.ellipse([ix + 2, cy - r, ix + 2 + 2 * r, cy + r], fill=DOT_EMOJI[val])
            else:
                icon = _twemoji(val, em)
                if icon:
                    img.paste(icon, (ix, int(y) + 1), icon)
            x += em + 3
        else:
            d.text((x, y), val, font=f, fill=colour)
            x += d.textlength(val, font=f)
    return x


def _rel(unix: int) -> str:
    delta = unix - time.time()
    past = delta < 0
    secs = abs(delta)
    if secs < 3600:
        n, unit = max(1, round(secs / 60)), "minute"
    elif secs < 86400:
        n, unit = round(secs / 3600), "hour"
    else:
        n, unit = round(secs / 86400), "day"
    plural = "" if n == 1 else "s"
    return f"{n} {unit}{plural} ago" if past else f"in {n} {unit}{plural}"


def humanize(text: str) -> str:
    """Render Discord markup the way Discord would: timestamps and mentions."""
    text = re.sub(r"<t:(\d+):[RFfDdTt]>", lambda m: _rel(int(m.group(1))), text)
    text = re.sub(r"<@!?(\d+)>", "@user", text)
    return text


def _bold_words(paragraph):
    """Yield (word, bold) honouring **...** markers; drop single backticks."""
    for i, chunk in enumerate(paragraph.replace("`", "").split("**")):
        bold = i % 2 == 1
        for word in chunk.split(" "):
            if word:
                yield word, bold


def draw_rich(d, img, x, y, text, size, colour, max_width):
    """Word-wrapped text with inline emoji and **bold**. Returns the y below the block."""
    fnt, bfnt = font(size), font(size, bold=True)
    line_h = int(size * 1.42)
    em = int(size * 1.1)
    space_w = d.textlength(" ", font=fnt)
    for paragraph in humanize(str(text)).split("\n"):
        cx = x
        first = True
        for word, bold in _bold_words(paragraph):
            ww = _word_width(d, word, fnt, bfnt, bold, em)
            space = 0 if first else space_w
            if cx + space + ww > x + max_width and not first:
                y += line_h
                cx = x
                space = 0
            cx = _draw_word(d, img, cx + space, y, word, fnt, bfnt, bold, colour, em, line_h - size)
            first = False
        y += line_h
    return y


def render_embed(embed, path: str, *, pad_bottom: int = 24) -> None:
    # Uncoloured embeds render with Discord's neutral grey bar.
    accent = tuple((embed.colour.value >> s) & 0xFF for s in (16, 8, 0)) if embed.colour else (79, 84, 92)

    # Measure pass on a scratch image.
    scratch = Image.new("RGB", (W, 20))
    md = ImageDraw.Draw(scratch)
    y = PAD + 24
    if embed.title:
        y = draw_rich(md, scratch, LEFT, y, embed.title, 30, WHITE, RIGHT - LEFT) + 6
    if embed.description:
        y = draw_rich(md, scratch, LEFT, y, embed.description, 21, TEXT, RIGHT - LEFT) + 10
    for f in embed.fields:
        y = draw_rich(md, scratch, LEFT, y, f.name, 21, WHITE, RIGHT - LEFT) + 2
        y = draw_rich(md, scratch, LEFT, y, f.value, 20, TEXT, RIGHT - LEFT) + 14
    if embed.footer and embed.footer.text:
        y += 6
        y = draw_rich(md, scratch, LEFT, y, embed.footer.text, 17, MUTED, RIGHT - LEFT)
    H = y + pad_bottom + PAD

    img = Image.new("RGB", (W, H), PAGE)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([PAD, PAD, W - PAD, H - PAD], radius=10, fill=EMBED)
    d.rounded_rectangle([PAD, PAD, PAD + ACCENT_W + 6, H - PAD], radius=6, fill=accent)
    d.rectangle([PAD + ACCENT_W, PAD, PAD + ACCENT_W + 8, H - PAD], fill=EMBED)

    y = PAD + 24
    if embed.title:
        y = draw_rich(d, img, LEFT, y, embed.title, 30, WHITE, RIGHT - LEFT) + 6
    if embed.description:
        y = draw_rich(d, img, LEFT, y, embed.description, 21, TEXT, RIGHT - LEFT) + 10
    for f in embed.fields:
        y = draw_rich(d, img, LEFT, y, f.name, 21, WHITE, RIGHT - LEFT) + 2
        y = draw_rich(d, img, LEFT, y, f.value, 20, TEXT, RIGHT - LEFT) + 14
    if embed.footer and embed.footer.text:
        y += 6
        draw_rich(d, img, LEFT, y, embed.footer.text, 17, MUTED, RIGHT - LEFT)

    img.save(path)
    print(f"wrote {path} ({W}x{H})")
