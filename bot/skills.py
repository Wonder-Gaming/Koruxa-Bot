"""Skill icons and grouping, matched to the in-game sidebar. The game draws its
icons as plain Unicode emoji (<span class="skill-icon">), so Discord can show
the same ones."""

from .fmt import title

# (group label, group emoji, skill keys in sidebar order)
SKILL_GROUPS = [
    ("Gathering", "🌿", ["woodcutting", "mining", "fishing", "farming", "thieving"]),
    ("Artisan", "🔨", [
        "cooking", "fletching", "crafting", "jewelery", "herblore", "smithing",
        "firemaking", "alchemy", "arcana", "construction", "tinkering",
    ]),
    ("Combat", "⚔️", ["slayer", "attack", "strength", "defence", "hitpoints", "magic", "ranged"]),
]

# Skills the API doesn't report on their own: the game uses another skill's level for them.
SKILL_SOURCE = {"alchemy": "magic"}

SKILL_EMOJI = {
    "woodcutting": "🪓",
    "mining": "⛏️",
    "fishing": "🎣",
    "farming": "🌱",
    "thieving": "🔑",
    "cooking": "🍳",
    "fletching": "🏹",
    "crafting": "🪄",
    "jewelery": "💍",
    "herblore": "🧪",
    "smithing": "🔨",
    "firemaking": "🔥",
    "alchemy": "⚗️",
    "arcana": "✨",
    "construction": "🏗️",
    "tinkering": "⚙️",
    "slayer": "🗡️",
    "attack": "⚔️",
    "strength": "💪",
    "defence": "🛡️",
    "hitpoints": "❤️",
    "magic": "✨",
    "ranged": "🏹",
}

# Where the in-game name differs from the API key.
SKILL_NAMES = {"jewelery": "Jewellery"}


def skill_emoji(key: str | None) -> str:
    return SKILL_EMOJI.get(str(key or "").lower(), "▫️")


def skill_name(key: str | None) -> str:
    k = str(key or "").lower()
    return SKILL_NAMES.get(k, title(k))


def skill_label(key: str | None) -> str:
    """e.g. "🪓 Woodcutting"."""
    return f"{skill_emoji(key)} {skill_name(key)}"
