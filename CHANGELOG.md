# Changelog

All notable changes to the Koruxa Discord Bot. Dates are UTC.

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
