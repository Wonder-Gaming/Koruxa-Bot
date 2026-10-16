"""Alert features a server can route to a channel, optionally with a ping role."""

from dataclasses import dataclass

from discord import app_commands


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    emoji: str
    description: str
    live: bool  # False = waiting on data the bot can't read yet
    clan: bool = False  # needs the clan token (/link-clan)


FEATURES: dict[str, Feature] = {
    f.key: f
    for f in [
        Feature("worldboss", "World Boss", "🌀", "World boss events: start, new phases, daily strike reset", live=True),
        Feature("help", "Help Menu", "📖", "A standing list of the bot's commands", live=True),
        Feature("feed", "Clan Feed", "📈", "Level-ups, clan levels, research and the weekly recaps", live=True),
        Feature("highscores", "Clan Highscores", "🏆", "Overall, Combat, Gathering and Artisan boards", live=True),
        Feature("clanboss", "Clan Boss", "⚔️", "Clan boss fights: start, updates and kills", live=True, clan=True),
        Feature("clanbank", "Clan Bank", "🏦", "Bank summary, deposits, withdrawals and requests", live=True, clan=True),
        Feature("enchant", "Enchant Queue", "✨", "Enchant queue updates", live=False),
    ]
}

# Features members can self-assign a ping role for from the role panel.
ROLE_FEATURES = ["worldboss", "clanboss"]

FEATURE_CHOICES = [app_commands.Choice(name=f.label, value=f.key) for f in FEATURES.values()]
