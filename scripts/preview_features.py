"""Render PNG previews of the experimental feature embeds (stock, loans, wiki).

  docker run --rm -v "$HOME/discord-bot:/app" -w /app discord-bot:latest \
    python scripts/preview_features.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.preview_lib import render_embed  # noqa: E402
from bot.cogs.stock import build_stock_embed  # noqa: E402
from bot.cogs.loans import build_ledger_embed  # noqa: E402
from bot.cogs.wiki import build_wiki_embed  # noqa: E402
from bot.wiki_cache import parse_item_html  # noqa: E402
from bot.stock import demo_rows  # noqa: E402
from bot.loans import demo_loans  # noqa: E402

CLAN = "Demo Clan [DEMO]"

render_embed(build_stock_embed(demo_rows(), CLAN, is_demo=True), "data/preview-stock.png")
render_embed(build_ledger_embed(demo_loans(), CLAN, is_demo=True), "data/preview-loans.png")
import os as _os  # noqa: E402
if _os.path.exists("data/dragon_bones.html"):
    render_embed(build_wiki_embed(parse_item_html(open("data/dragon_bones.html").read(), "dragon_bones",
                 "https://koruxa.com/wiki/items/dragon_bones.html")), "data/preview-wiki.png")
