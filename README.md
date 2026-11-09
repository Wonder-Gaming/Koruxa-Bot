# Koruxa Discord Bot

A self-hosted Discord bot for [Koruxa](https://koruxa.com) clans. It reads the Koruxa Player API to give your server:

- **Clan highscores**: live image boards (Overall, Combat, Gathering, Artisan; the long groups take two boards each) of your linked clan members, edited in place.
- **Clan feed**: skill level-ups, clan level-ups, Institute research (started, pool filled, complete), and weekly recaps / top gainers.
- **Clan boss alerts**: a live boss card with HP and shields, a top-5 damage leaderboard for the current boss, a role ping when a new boss spawns or attempts refresh, and a countdown to the next spawn.
- **Clan bank**: a live bank summary and the latest deposits, withdrawals and requests.
- **Clan Institute**: top donors, every research project's level progress, and the current research's material pool (what's donated and what's still missing), plus a thank-you to the top donors when a pool fills.
- **World boss alerts**: role pings for world boss events.
- **Personal tools**: profile, XP/h rates with what-if boosts, mastery route planning, XP gains, clan-bank budget.

Run your own copy with its own Discord bot account. One copy can serve several Discord servers, each with its own clan (see [Hosting several servers](#hosting-several-servers)).

What's new: see the [changelog](CHANGELOG.md).

## Screenshots

Every image below is drawn by the bot itself, with the game's real skills, items and research. The clan (*Mossy Hollow [MOSS]*), players and numbers are made up; regenerate them with [`scripts/demo_images.py`](scripts/demo_images.py).

| Clan highscores | Skill boards (Combat and Artisan take two boards each) |
|---|---|
| ![Overall highscores](docs/images/highscores-overall.png) | ![Gathering highscores](docs/images/highscores-gathering.png) |
| **Clan boss** (live HP, shields, attempts) | **Boss damage** (top 5 on the current boss) |
| ![Clan boss card](docs/images/clan-boss.png) | ![Boss damage leaderboard](docs/images/boss-damage.png) |
| **Clan bank** | **Bank activity** (deposits, withdrawals, requests) |
| ![Clan bank summary](docs/images/clan-bank.png) | ![Recent bank activity](docs/images/bank-activity.png) |
| **Level-ups** (clan feed) | **Weekly top gainers** |
| ![Level-ups](docs/images/level-ups.png) | ![Weekly top gainers](docs/images/top-gainers.png) |
| **Weekly clan recap** | **Clan level-up** |
| ![Weekly clan recap](docs/images/clan-week.png) | ![Clan level up](docs/images/clan-level.png) |
| **Institute: top donors** | **Institute: every project's progress** |
| ![Top Institute donors](docs/images/institute-donors.png) | ![Institute overview](docs/images/institute-overview.png) |
| **Institute: current research** (donated / needed / left) | **Research pool filled** (clan feed) |
| ![Current research](docs/images/institute-research.png) | ![Research pool filled](docs/images/institute-pool-filled.png) |

---

## Contents

1. [Requirements](#requirements)
2. [Create the Discord bot](#1-create-the-discord-bot)
3. [Download and configure](#2-download-and-configure)
4. [Start the bot](#3-start-the-bot)
5. [Set up your server](#4-set-up-your-server)
6. [Commands](#commands)
7. [Configuration reference](#configuration-reference)
8. [Hosting several servers](#hosting-several-servers)
9. [Updating, logs and backups](#updating-logs-and-backups)
10. [Security and privacy](#security-and-privacy)
11. [Troubleshooting](#troubleshooting)

---

## Requirements

- A machine that stays on (a home server, Raspberry Pi 4/5, or a small VPS) with
  [Docker](https://docs.docker.com/engine/install/) and the Docker Compose plugin.
- About 100–200 MB of RAM (the container is capped at 512 MB). The bot needs **no open ports**: it only makes outgoing connections to Discord and koruxa.com.
- Admin rights (**Manage Server**) on the Discord server you want to add it to.
- For the clan features: your clan's API token from the game (see [Clan token](#clan-token)).

You can also run it without Docker (Python 3.12+, `pip install -r requirements.txt`, `python -m bot`), but Docker is the supported way.

---

## 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Give it a name (e.g. *Koruxa Bot*).
2. Open the **Bot** tab:
   - Click **Reset Token**, then copy the token. This is your `DISCORD_TOKEN`. **Keep it secret**: anyone with it controls your bot.
   - Leave all three **Privileged Gateway Intents** turned **off**. The bot doesn't need them.
   - Optional: turn off **Public Bot** so only you can invite it (turn it on, or use `ALLOWED_GUILDS`, if you'll host other servers).
3. Invite the bot to your server. Open this link, replacing `YOUR_APPLICATION_ID` with the **Application ID** from the **General Information** tab:

   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands&permissions=268684288
   ```

   That grants exactly what the bot uses:

   | Permission | Why |
   |---|---|
   | View Channels, Send Messages, Read Message History | Post and update its alert messages |
   | Embed Links, Attach Files | Embeds and the highscore/boss images |
   | Mention @everyone, @here and All Roles | Ping the alert roles (they're created non-mentionable so members can't spam them) |
   | Manage Roles | Create the alert roles and hand them out from the role panel |

4. Get your **server ID** for `GUILD_ID`: in Discord, enable **User Settings → Advanced → Developer Mode**, then right-click your server icon → **Copy Server ID**.

---

## 2. Download and configure

```bash
git clone <this repository's URL> koruxa-bot
cd koruxa-bot
cp .env.example .env
chmod 600 .env
```

Generate an encryption key (the bot encrypts every Koruxa token it stores with it):

```bash
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Edit `.env` and fill in at least:

```ini
DISCORD_TOKEN=your-bot-token
ENCRYPTION_KEY=the-key-you-just-generated
GUILD_ID=your-server-id
```

> ⚠️ **Back up `ENCRYPTION_KEY`.** If it's lost or changed, the stored tokens can't be read and everyone has to `/link` again.

All other settings are optional; see the [configuration reference](#configuration-reference).

---

## 3. Start the bot

```bash
docker compose up -d --build
docker compose logs -f bot
```

You should see `Synced N command(s)` and `Logged in as …`. Press `Ctrl+C` to stop following the logs (the bot keeps running).

The container runs as a non-root user with a read-only filesystem, no Linux capabilities and a 512 MB memory limit (about 180 MB is enough for five clans). Its only writable storage is the `data/` folder (the SQLite database).

---

## 4. Set up your server

Do these in Discord, as a server admin. Commands marked *(admin)* are hidden from members who lack **Manage Server**.

### a. Make some channels

A suggested layout (any names work):

| Channel | Used for |
|---|---|
| `#koruxa-bot` | Role panel, help menu, patch notes, and where members run commands |
| `#highscores` | Clan Highscores |
| `#clan-feed` | Clan Feed |
| `#boss-alerts` | World Boss and Clan Boss |
| `#clan-bank` | Clan Bank |
| `#clan-institute` | Clan Institute |

Make sure the bot's role can view and post in them.

### b. Point each feature at a channel

Run `/setchannel` once per feature:

```
/setchannel feature:Clan Highscores channel:#highscores
/setchannel feature:Clan Feed       channel:#clan-feed
/setchannel feature:Clan Boss       channel:#boss-alerts
/setchannel feature:World Boss      channel:#boss-alerts
/setchannel feature:Clan Bank       channel:#clan-bank
/setchannel feature:Clan Institute  channel:#clan-institute
/setchannel feature:Help Menu       channel:#koruxa-bot
```

When the bot updates, it posts the patch notes in the Help Menu channel (or a **Bot Updates** channel, if set) and deletes them after 12 hours.

Run `/setchannel` with no channel to turn a feature off. `/alerts` shows the current setup and any permission problems.

### c. Post the role panel

```
/rolepanel channel:#koruxa-bot
```

This creates the **World Boss** and **Clan Boss** roles (or reuses existing roles with those names) and posts buttons members click to opt in or out of pings. If it warns that a role is above the bot's role, drag the bot's role higher in **Server Settings → Roles**.

Check everything with `/testalert feature:Clan Boss`.

### d. Link the clan token

<a id="clan-token"></a>
The clan features (bank, boss, clan XP, Institute) need your **clan's** API token, linked separately in each server. It's a different token from a personal one: it's managed by the clan in-game, in the clan's settings.

```
/link-clan
```

A pop-up asks for the token. The bot checks it with Koruxa, stores it encrypted, and replies with the clan's name. From then on:

- The highscores, clan feed and `/topgains` only count linked characters **in that clan**.
- Clan boss, bank, XP and Institute updates start within a minute.

Running `/link-clan` again with another token replaces it. `/clear-clan` removes it (clan features stop; highscores then show every linked character). Both only affect the server they're run in, and need **Manage Server** there.

### e. Members link their characters

Each member runs:

```
/link
```

and pastes their **Player API token** (in game: **Settings → Account → API access → New token**, starts with `kxu_`). Up to 5 characters per Discord user. Tokens are stored encrypted and only read that player's own data. `/unlink` removes a character and deletes its token.

Linked characters in the clan appear on the highscores at the next refresh (every 15 minutes by default).

---

## Commands

### 👤 Your account

| Command | What it does |
|---|---|
| `/link` | Link a Koruxa character with its Player API token (up to 5) |
| `/unlink [character]` | Remove a linked character and delete its stored token |
| `/characters` | List your linked characters |
| `/profile [character] [public]` | Show your Koruxa profile |
| `/budget [character]` | Your clan-bank withdrawal budget for today (needs the *clan bank* block enabled on your token in game) |

### 🧮 Tools

| Command | What it does |
|---|---|
| `/rates <skill> [sort] [wells] [buffs] [brew] [extra_speed] [extra_xp] [cape] [character] [public]` | Your best XP per hour for a skill, with optional what-if boosts. `sort`: XP or mastery XP per hour. `cape`: count your best owned cape instead of the one you're wearing |
| `/mastery <unlock> <skill> [character] [public]` | Quickest route to the total mastery that unlocks a skill's expert actions (3,000 / 3,500 / 4,000, or all three) |
| `/bossupgrades [scrolls] [character] [public]` | Which clan boss upgrade gives the most damage per scroll next, and a buying plan (for the next 10 buys, or for the scrolls you have) |

### 📈 Progress

| Command | What it does |
|---|---|
| `/gains [period] [character] [public]` | XP gained today, this week or this month (your characters or a clanmate's) |
| `/topgains [period] [public]` | The server's top XP gainers |

### 🏰 Clan *(needs the clan token)*

| Command | What it does |
|---|---|
| `/clan [public]` | Clan level and this week's top XP contributors |
| `/clanbank [public]` | What's in the clan bank |
| `/institute [public]` | Clan Institute research levels and what's next |
| `/link-clan` *(admin)* | Set this server's clan API token |
| `/clear-clan` *(admin)* | Remove this server's clan API token |

### 🏦 Bank contributions *(needs the clan token)*

| Command | What it does |
|---|---|
| `/contributions` | Who funds the clan — net value each player deposited vs withdrew, valued at wiki market prices, grouped across their linked characters |

### 📦 Bank stock *(needs the clan token)*

| Command | What it does |
|---|---|
| `/stock` | Stock-target board — each tracked item's current bank amount vs its target (🔴 critical / 🟡 low / 🟢 stocked) |
| `/stock-set <item> <target>` *(admin)* | Set a target quantity to keep stocked for a bank item |
| `/stock-remove <item>` *(admin)* | Remove a stock target |

For a **live board**, `/setchannel stock #channel`. In a Forum channel you get one self-updating post per item (title shows the live count/percent/status, plus recent donors and takers); in a text channel, one combined board. Refreshes every ~10 minutes and whenever a target changes.

### 📒 Bank loans *(needs the clan token)*

| Command | What it does |
|---|---|
| `/loans` | Create/refresh a **Loan Ledger** thread tracking sealed items out on loan (auto-updates as the bank log changes; nets out alt-to-alt moves) |

### 🧪 Potion usage *(needs the clan token)*

| Command | What it does |
|---|---|
| `/potions` | Weekly potion usage — net potions each player drew from the bank (withdrawals − deposits), grouped across their linked characters |

### 📜 Wiki

| Command | What it does |
|---|---|
| `/wiki <item>` | Look up a Koruxa item: category, rarity, level, market price, stats, recipes and drop sources |
| `/wiki-sync` *(admin)* | Crawl/refresh the local wiki cache |

### 📚 Guides

Set a channel with `/setchannel guides #channel` (a **text or Forum channel**), then publish. Each guide gets its own thread/post — in a Forum, one post per guide — edited in place and refreshed daily, all from the cached wiki data, no player setup.

| Command | What it does |
|---|---|
| `/guides` *(admin)* | Publish/refresh the guides: Best-in-Slot (Melee/Ranged/Magic) per level, per-slot upgrade paths, and combat augments |

### 🎯 Skill focus

Members set the skills they focus on (optionally per linked character); the board shows who covers each skill — grouped Gathering / Artisan / Combat (Slayer is under Combat).

| Command | What it does |
|---|---|
| `/focus [skill] [character]` | Show the board, or toggle a skill you focus on (optionally for one of your characters) |
| `/focus-set <member> <skill> [character] [remove]` *(admin)* | Assign or unassign a member's focus skill |

### 🔐 Access control *(admin)*

Gate the admin commands above by **Discord role** and/or **in-game clan rank** (not just server admins). Scopes: `bank` (stock), `jobs`, `wiki`.

| Command | What it does |
|---|---|
| `/access-show` | Show who can use each gated command group |
| `/access-set <scope> [mode] [role] [rank]` | Set a scope to `managers` / `everyone` / `custom` (allow specific Discord roles and/or in-game ranks) |
| `/access-reset <scope>` | Reset a scope to Manage-Server-only |

### 🌀 World Boss

| Command | What it does |
|---|---|
| `/worldboss` | Show the current World Boss event |

### 🛠️ Server setup *(admin)*

| Command | What it does |
|---|---|
| `/setchannel <feature> [channel]` | Choose the channel for a type of alert (leave `channel` empty to turn it off) |
| `/rolepanel [channel]` | Post the buttons members use to pick their alert roles |
| `/alerts` | Show where each type of alert goes |
| `/testalert <feature>` | Post a test alert, with its role ping |

### General

| Command | What it does |
|---|---|
| `/help` | List the commands and their options (admins also see the admin ones) |
| `/ping` | Check that the bot is alive |

Most read-only commands answer privately; add `public:True` to show the answer to the channel.

---

## Configuration reference

All settings live in `.env` (see `.env.example`). Restart after changing them: `docker compose up -d`.

| Setting | Required | Default | Meaning |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | | Your bot's token from the Developer Portal |
| `ENCRYPTION_KEY` | ✅ | | Fernet key that encrypts stored Koruxa tokens. Back it up |
| `GUILD_ID` | | *(empty)* | A server ID to register commands in instantly (handy while testing). Commands then appear **only** in that server, so leave it empty if the bot serves several servers. Empty = commands sync globally (can take a while to appear the first time) |
| `ALLOWED_GUILDS` | | *(empty)* | The only servers this copy may be in (IDs, comma-separated). It leaves any other server straight away. Empty = any server |
| `BOT_STATUS` | | *(empty)* | A custom status under the bot's name, e.g. who to contact if it breaks |
| `CLAN_TAG` | | *(empty)* | Only for servers that haven't linked a clan token: limit their highscores/feed/`/topgains` to this clan tag. Empty = every linked character |
| `HIGHSCORES_REFRESH_MINUTES` | | `15` | How often each linked player's profile is re-fetched (minimum 5) |
| `WEEKLY_RESET_DAY` / `WEEKLY_RESET_HOUR` | | `Monday` / `0` | When the game's week resets (UTC); weekly recaps post then |
| `HIGHSCORES_DEMO` | | *(empty)* | `1` adds 8 made-up players to preview full boards (only in servers without a clan token) |
| `LOG_LEVEL` | | `INFO` | `DEBUG`, `INFO` or `WARNING` |

### API limits

Koruxa allows **240 requests/hour per personal token** and **120/hour per clan token**. The bot paces itself under both:

- Highscores: one `/me` per linked character per refresh (4/hour each at the 15-minute default).
- Clan token: about 85/hour per clan (boss every minute, XP every 5, bank and Institute every 10), each on its own token's limit. If other tools use the same clan token, you can hit the limit; the bot then backs off and logs `429`.

---

## Hosting several servers

One copy of the bot can serve several Discord servers, for example a few clans that don't want to run their own:

- **Each server links its own clan** with `/link-clan`. Its clan boss, bank, research and recaps come from that clan and post only there, and its highscores and feed count that clan's members. A server can't see or change another server's clan.
- **Two servers with the same clan** (say a main and an alt server) share one poller: the clan is polled once and both get the updates.
- **Members** `/link` as usual; links belong to the Discord user, so someone in two servers links once.
- **Lock it down** with `ALLOWED_GUILDS` in `.env`, listing the server IDs you host. If anyone adds the bot elsewhere it leaves at once, so strangers can't use your machine. Leave `GUILD_ID` empty so commands appear in every server.
- **Trust:** members' Koruxa tokens are stored (encrypted) on your machine, with your key. Make sure the clans you host are happy with that.
- **Cost:** each clan adds about 85 requests/hour on its own clan token, plus 4/hour per linked player on their own tokens, and roughly 15 MB of memory. The 512 MB cap fits five clans comfortably.

To invite the bot to another server, use the invite link from [step 1](#1-create-the-discord-bot) (the **Public Bot** setting must be on for anyone other than you to add it), add the new server's ID to `ALLOWED_GUILDS` if you use it, and restart: `docker compose up -d`.

---

## Updating, logs and backups

```bash
git pull                      # get the latest code
docker compose up -d --build  # rebuild and restart
docker compose logs -f bot    # follow the logs
docker compose down           # stop the bot
```

Everything the bot remembers is in `data/bot.db`. To back up, copy `data/` **and** your `.env` (without `ENCRYPTION_KEY` the stored tokens are unreadable).

---

## Security and privacy

- **Never commit `.env` or `data/`.** Both are in `.gitignore`. `.env` holds your bot token and encryption key; `data/bot.db` holds the (encrypted) Koruxa tokens.
- Koruxa tokens, personal and clan, are encrypted at rest with `ENCRYPTION_KEY` and never written to the logs (logs show a short fingerprint instead).
- Tokens are entered through pop-up forms, so they never appear in chat or command history.
- If a token is revoked in game, the bot notices, unlinks it and DMs the owner.
- If your Discord bot token leaks, reset it in the Developer Portal. If the clan token leaks, rotate it in game and `/link-clan` the new one.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Commands don't show up | Set `GUILD_ID` for instant sync (single server only), restart, and reload Discord (`Ctrl+R`). Check the logs say `Synced N command(s)`. In other servers, `GUILD_ID` must be empty |
| The bot leaves a server right after joining | That server isn't in `ALLOWED_GUILDS`. Add its ID and restart |
| `ENCRYPTION_KEY is not a valid Fernet key` | Generate one with the command in [step 2](#2-download-and-configure) |
| Alerts don't post | Run `/alerts`; it lists missing channel permissions |
| Role pings don't notify | The bot needs **Mention @everyone, @here and All Roles** in that channel |
| Role panel can't assign a role | Move the bot's role above the alert roles in **Server Settings → Roles** |
| Highscores are empty | Members must `/link`, and only characters in the linked clan count. The footer shows how many linked clan members it found |
| `429 … backing off` in the logs | A token hit its hourly API limit (often a clan token shared with other tools). It recovers on its own |
| "Koruxa rejected that token" on `/link-clan` | That's a personal `kxu_` token; use the clan's token |

---

## License

The bot's code is public domain under [The Unlicense](LICENSE): use, copy, change, sell or share it however you like, no credit needed.

The bundled font and emoji images in `assets/` keep their own licences (see below); Twemoji's CC-BY 4.0 asks that you credit Twitter if you redistribute them.

## Credits

- Big thanks to **Woe** for generously sharing his own Koruxa clan bot — the bank contributions, stock targets, loan ledger, potion-usage tracking, wiki lookup, clan jobs and rank-aware access-control features were all inspired by his work (reimplemented here from scratch).
- Game data from the Koruxa Player API. This is a fan-made bot, not affiliated with Koruxa.
- Fonts and emoji images: see [`assets/README.md`](assets/README.md) (Inter, SIL OFL 1.1; Twemoji, CC-BY 4.0).
