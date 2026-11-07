"""Render the /potions usage card (demo data) to a PNG.

  docker run --rm -v "$HOME/discord-bot:/app" -w /app discord-bot:latest \
    python scripts/preview_potions.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.potion_card import render_potion_card  # noqa: E402
from bot.potions import demo_usage  # noqa: E402

png = render_potion_card(demo_usage(), clan_name="Demo Clan [DEMO]")
with open("data/preview-potions.png", "wb") as f:
    f.write(png)
print(f"wrote data/preview-potions.png ({len(png)} bytes)")
