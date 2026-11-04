# Changelog

All notable changes to the Koruxa Discord Bot. Dates are UTC.

## [1.9.0] - 2026-11-04

### Changed
- You can now link **up to 5 characters** per Discord user (was 4), matching the game's new character slot.

## [1.8.2] - 2026-11-04

### Changed
- `/topmastery` now takes the same speed what-if options as `/rates` — `wells`, `buffs`, `brew` and `extra_speed` — and shows the scenario on the result. Mastery XP/hour depends on action speed only, so XP boosts are left out.

## [1.8.1] - 2026-11-04

### Fixed
- `/topmastery file:True` crashed with a missing-import error; the text-file option works now.

## [1.8.0] - 2026-11-04

### Added
- **`/topmastery`**: your unlocked actions across every skill ranked by mastery XP per hour (fastest to master) — the top 30 in chat, or `file:True` for the top 50 as a text file.

## [1.7.2] - 2026-11-04

### Changed
- `/gearcheck` now covers **Arcana and Alchemy** (wands & athames, on their own tier ladder), adds the missing **Vexite (Lv 85)** gathering tier, puts a skill emoji next to each skill, and lists **only the tools that need upgrading or are missing** (with a count of how many are already optimal).

## [1.7.1] - 2026-11-04

### Fixed
- The patch-notes "posted to N server(s)" log counted posts carried over from the previous release, inflating the number (it reported 4 for a 2-server post). It now counts only the posts made for the current release; earlier posts are still kept for their 12-hour cleanup.

## [1.7.0] - 2026-11-04

### Added
- **`/gearcheck`**: compares your equipped gathering/artisan tools to your skill levels and flags where a higher tier is available — e.g. "Firemaking 45: Dustite Tinderbox (Lv 1) → upgrade to **Shalore Tinderbox** (Lv 40)". It also points out tool slots you've left empty. Magic wands use a separate tier ladder and are skipped for now.

## [1.6.1] - 2026-11-04

### Fixed
- **World Boss card now updates in place.** One status card appears for the event and edits itself through upcoming → live → ended, instead of posting a fresh card each time. Phase changes, the event start and the daily strike reset are now separate role pings that clear the previous one (like the clan boss attack pings), rather than extra cards piling up.

## [1.6.0] - 2026-11-04

### Added
- **World Boss card**: `/worldboss`, the live status message and the alerts now show a rendered card — overall clear progress plus a row per phase, the current phase highlighted, each with its boss, progress bar and strikes.

### Fixed
- **World Boss phase alerts now fire.** Phase tracking looked for a field the event doesn't carry, so "a new phase opened" pings never triggered. It now follows the event's phase list: when the current phase is cleared, it pings the World Boss role with the next phase and its boss.
- **Clan level-up card showed the wrong member slots** (e.g. "18 members of 3 slots"). The level curve's member-slots value is a broken constant on the API, so the card no longer shows slots — it shows the member count, max level and clan-XP progress from the fields the API reports reliably.

## [1.5.2] - 2026-11-03

### Changed
- `/bossupgrades` uses more values confirmed in game: Damage costs 5 and Precision 25 scrolls per level, Devastation adds ×0.15 crit damage per level, and Rage adds 5 rage cap per level (no extra base). The damage-per-attempt estimate drops a little; the upgrade ranking is unchanged.

## [1.5.1] - 2026-11-03

### Changed
- `/bossupgrades`: the upgrade caps (Precision 40, Rage 15, Devastation 20, Crit 25, Time 30; Damage and Power uncapped) are confirmed in game, so the footer no longer lists them as estimates.
- Patch notes are only posted in Discord for feature releases (e.g. 1.5 → 1.6). Small fixes like this one update quietly.

## [1.5.0] - 2026-11-03

### Added
- **`/bossupgrades`**: which clan boss upgrade gives the most damage per scroll next, and a buying plan (the next 10 buys, or `scrolls:` to plan what you have). Max hit and Power match the game exactly; maxed upgrades are listed separately.
- **Patch notes in Discord**: when the bot updates, it posts what's new in the Help Menu channel (or a new **Bot Updates** channel from `/setchannel`) and deletes it after 12 hours.

## [1.4.0] - 2026-10-25

### Added
- **Clan Institute channel** (`/setchannel feature:Clan Institute`): three messages, edited in place every 10 minutes:
  1. the top 10 donors of all time;
  2. every research project on one card: level towards max, status (researching, filling the pool, paused, not started, maxed) and the bonus it gives now;
  3. the current research: each material's donated / needed / left, then the finish time once the pool is full. When the clan moves on, this message switches to the next project.
- **"Research pool filled"** card in the clan feed, thanking the top 3 donors to that pool, with the research's finish time.

### Changed
- README screenshots now use the game's real skills, items and research; only the clan, players and numbers are made up.

### Fixed
- Clan bank, boss and Institute cards weren't posted to a server that picked its channel after its clan was already being tracked, until the data next changed.

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
