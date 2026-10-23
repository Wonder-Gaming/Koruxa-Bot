# Changelog

All notable changes to the Koruxa Discord Bot. Dates are UTC.

## [1.3.1] - 2026-10-23

### Fixed
- **Boss damage could add up to more than the boss's HP** (e.g. 16T on a 9.15T boss). The game's weekly boss damage counter includes overkill, so the card now keeps each member's share of the counters but scales the totals down to the HP actually removed. The footer says "scaled to the boss's HP" when it does.
- A share just under 100% (e.g. 99.9998%) showed as "100%"; it now shows ">99%", so 100% only means one player did all of it.

### Added
- Each boss damage update logs how far the counters ran ahead of the HP removed, to check over a few fights how fair the scaling is.

## [1.3.0] - 2026-10-22

### Added
- **One bot, several clans.** Each Discord server links its own clan token with `/link-clan`. Its clan boss, damage card, bank, clan level-ups, research and weekly recap come from that clan and post only in that server, and its highscores, level-ups and `/topgains` count that clan's members. `/clan`, `/clanbank` and `/institute` answer for the server they're run in. Servers that link the same clan share one poller.
- `ALLOWED_GUILDS`: the servers this copy may be in. It leaves any other server straight away (and at startup).
- `BOT_STATUS`: a custom status under the bot's name (e.g. who to contact).
- README: "Hosting several servers".

### Changed
- `/link-clan` and `/clear-clan` only affect the server they're run in, and any server's admins can use them (before, the token was shared by every server and limited to the `GUILD_ID` server).
- The bot's status no longer shows the clan boss (one status line can't show several clans); use `BOT_STATUS` instead.
- Memory cap raised from 256 MB to 512 MB (about 180 MB covers five clans).

### Upgrading
- Nothing to do for a single server: on first start, the clan token linked before moves to the server that uses the clan features (or to `GUILD_ID`). If the bot can't tell which server that is, run `/link-clan` again.

## [1.2.1] - 2026-10-20

### Fixed
- `/mastery` could plan an action past mastery level 500, the game's cap (e.g. "train X to 518"). Actions now stop at 500 and the next quickest action takes over, and the "train just one action" option only suggests an action that can get there without passing 500.

## [1.2.0] - 2026-10-17

### Changed
- **Clan feed level-ups are one running post**, edited in place as new level-ups come in, instead of a new post each time. A new post starts when another message has gone out in the channel since (so the one being edited is always the newest), when the UTC day changes, or when the post is full. Each post holds up to 7 rows in one column; extra rows continue in the next post.
- The same player levelling the same skill again updates their row (e.g. *Woodcutting 98 → 100*) instead of adding another.
- The level-up card's subtitle says who it covers: "linked [TAG] members" with a clan token linked, "linked players" without.

### Added
- The bot logs how many API requests each token made every 15 minutes (tokens shown as short fingerprints), to check usage against the hourly limits.

## [1.1.0] - 2026-10-17

### Added
- **Boss damage card**, under the clan boss card: the top 5 members by damage on the current boss, with each one's share of the total. The API has no per-boss damage, so the bot snapshots every member's weekly boss damage when a boss spawns and counts up from there (it costs no extra API calls). It updates every 5 minutes and freezes as "Final" 10 minutes after the kill. *New: the first real fights may still show quirks.*

### Changed
- **Every image is the same size** (1600 × 912), so Discord shows them all at the same scale instead of shrinking the taller ones.
- **Highscores**: Combat and Artisan are split over two boards each (*Combat 1/2*, *Artisan 1/2*), so every board is short enough to read. The channel now has six boards; the bot reposts them in order once after updating.
- Long player names shrink to fit instead of being cut off ("Riftbreaker_Supreme" no longer becomes "Riftbreaker_Su…").
- **Clan bank**: stat tiles on the left, top 8 items on the right (was 10).
- **Bank activity**: shows the latest 8 rows (was 15), and the footer says how many.
- **Level-ups**: more than 7 go into a second column; past 14, the footer says "…and N more".
- **Weekly clan recap**: all six stat tiles sit in one row.

### Fixed
- **Alchemy** now uses the Magic level, as the game does. The API reports no Alchemy level, so the highscores row was empty and `/rates alchemy` / `/mastery alchemy` showed every action as locked.
- `/rates` footer read "Your gear and tools as of (the game caches this…)"; it now reads "Your gear and tools now".

## [1.0.0] - 2026-10-17

First public release.

### Added
- Clan highscores (Overall, Combat, Gathering, Artisan) as live images, limited to the clan of the linked clan token.
- Clan feed: skill level-ups, clan level-ups, Institute research, weekly top gainers and weekly clan recap.
- Clan boss card with live HP and shields, a role ping on spawn and when attempts refresh, and a live "down · next boss in X" countdown while it respawns.
- Clan bank summary and recent activity.
- World boss alerts.
- Personal commands: `/link`, `/unlink`, `/characters`, `/profile`, `/budget`, `/rates`, `/mastery`, `/gains`, `/topgains`.
- Admin commands: `/setchannel`, `/rolepanel`, `/alerts`, `/testalert`, `/link-clan`, `/clear-clan`.
- Setup guide, command list and screenshots in the README; released into the public domain (Unlicense).
