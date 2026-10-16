import asyncio
import logging
import signal
import sys

import discord

from . import logsafe
from .client import KoruxaBot
from .config import ConfigError, load_config

log = logging.getLogger("bot")


async def run(config) -> None:
    bot = KoruxaBot(config)
    loop = asyncio.get_running_loop()
    # `docker stop` sends SIGTERM: close the gateway connection cleanly.
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
    async with bot:
        await bot.start(config.discord_token)
    log.info("Shut down cleanly")


def main() -> int:
    try:
        config = load_config()
    except ConfigError as e:
        logging.basicConfig(level=logging.INFO)
        log.error("Config error: %s", e)
        return 1

    discord.utils.setup_logging(level=getattr(logging, config.log_level, logging.INFO), root=True)
    logsafe.install()

    try:
        asyncio.run(run(config))
    except discord.LoginFailure:
        log.error("Discord rejected DISCORD_TOKEN. Reset it in the Developer Portal and update .env")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
