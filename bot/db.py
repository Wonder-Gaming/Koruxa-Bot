"""SQLite storage, kept in the data directory (the container's only volume).

Players can have up to 4 characters, so tokens and everything tracked are kept
per character: links (discord user + character), snapshots, XP history and
announced levels are keyed by the game's character id.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

MAX_CHARACTERS = 4

SCHEMA = """
-- One row per linked character. A Discord user can link up to 4.
CREATE TABLE IF NOT EXISTS links (
    discord_id   INTEGER NOT NULL,
    character_id INTEGER NOT NULL,
    token_enc    BLOB    NOT NULL,   -- Fernet-encrypted Player API token
    username     TEXT,
    linked_at    TEXT    NOT NULL,   -- ISO 8601 UTC; the first one linked is the default character
    PRIMARY KEY (discord_id, character_id)
);

-- Per-server settings for each alert feature (see bot/features.py).
CREATE TABLE IF NOT EXISTS guild_features (
    guild_id          INTEGER NOT NULL,
    feature           TEXT    NOT NULL,
    channel_id        INTEGER,           -- where alerts are posted
    role_id           INTEGER,           -- role pinged by alerts (self-assigned via the role panel)
    status_message_id INTEGER,           -- live status message the bot keeps editing
    PRIMARY KEY (guild_id, feature)
);

-- Latest known profile data per linked character (highscores, feed, gains).
CREATE TABLE IF NOT EXISTS snapshots (
    character_id INTEGER PRIMARY KEY,
    discord_id   INTEGER NOT NULL,   -- who linked it
    username     TEXT    NOT NULL,
    clan_tag     TEXT,
    skills       TEXT    NOT NULL,   -- JSON {skill_key: [level, xp]}
    updated_at   TEXT    NOT NULL,   -- the API's generated_at
    total_level  INTEGER,
    total_xp     INTEGER,
    combat_level INTEGER
);

-- One row per character per UTC day: the last values seen that day (XP gains).
CREATE TABLE IF NOT EXISTS history (
    character_id INTEGER NOT NULL,
    day          TEXT    NOT NULL,   -- YYYY-MM-DD (UTC)
    total_xp     INTEGER NOT NULL,
    skills       TEXT    NOT NULL,   -- JSON {skill_key: [level, xp]}
    PRIMARY KEY (character_id, day)
);

-- Skill levels already announced in the clan feed (so each level-up posts once).
CREATE TABLE IF NOT EXISTS announced (
    character_id INTEGER PRIMARY KEY,
    levels       TEXT    NOT NULL    -- JSON {skill_key: level}
);

-- Small JSON blobs of tracker state, so restarts don't repeat alerts.
CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Link:
    discord_id: int
    character_id: int
    token_enc: bytes
    username: str | None
    linked_at: str


@dataclass
class FeatureConfig:
    guild_id: int
    feature: str
    channel_id: int | None
    role_id: int | None
    status_message_id: int | None


@dataclass
class Snapshot:
    character_id: int
    discord_id: int
    username: str
    clan_tag: str | None
    skills: dict[str, list[int]]  # skill_key -> [level, xp]
    updated_at: str
    total_level: int | None = None
    total_xp: int | None = None
    combat_level: int | None = None


@dataclass
class HistoryRow:
    day: str
    total_xp: int
    skills: dict[str, list[int]]


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._migrate_single_character_tables()
        await self._conn.commit()

    async def _table_exists(self, name: str) -> bool:
        async with self.conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)) as cur:
            return await cur.fetchone() is not None

    async def _migrate_single_character_tables(self) -> None:
        """Move data from the old one-character-per-user tables (keyed by Discord id), then drop them."""
        if not await self._table_exists("linked_users"):
            return
        c = self.conn
        await c.execute(
            "INSERT OR IGNORE INTO links (discord_id, character_id, token_enc, username, linked_at) "
            "SELECT discord_id, character_id, token_enc, username, linked_at FROM linked_users WHERE character_id IS NOT NULL"
        )
        if await self._table_exists("player_snapshots"):
            await c.execute(
                "INSERT OR IGNORE INTO snapshots (character_id, discord_id, username, skills, updated_at, "
                "total_level, total_xp, combat_level) SELECT character_id, discord_id, username, skills, updated_at, "
                "total_level, total_xp, combat_level FROM player_snapshots WHERE character_id IS NOT NULL"
            )
        mapping = "(SELECT character_id FROM linked_users u WHERE u.discord_id = t.discord_id)"
        if await self._table_exists("xp_history"):
            await c.execute(
                f"INSERT OR IGNORE INTO history (character_id, day, total_xp, skills) "
                f"SELECT {mapping}, day, total_xp, skills FROM xp_history t WHERE {mapping} IS NOT NULL"
            )
        if await self._table_exists("announced_levels"):
            await c.execute(
                f"INSERT OR IGNORE INTO announced (character_id, levels) "
                f"SELECT {mapping}, levels FROM announced_levels t WHERE {mapping} IS NOT NULL"
            )
        for old in ("announced_levels", "xp_history", "player_snapshots", "linked_users"):
            await c.execute(f"DROP TABLE IF EXISTS {old}")
        log.info("Migrated single-character tables to per-character tables")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() was not called")
        return self._conn

    # ---------- links ----------

    _LINK_COLUMNS = "discord_id, character_id, token_enc, username, linked_at"

    async def save_link(self, discord_id: int, character_id: int, token_enc: bytes, username: str | None) -> None:
        """Add a character, or replace the token of one already linked (keeping its place in the order)."""
        await self.conn.execute(
            """
            INSERT INTO links (discord_id, character_id, token_enc, username, linked_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(discord_id, character_id) DO UPDATE SET
                token_enc = excluded.token_enc, username = excluded.username
            """,
            (discord_id, character_id, token_enc, username, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        await self.conn.commit()

    async def user_links(self, discord_id: int) -> list[Link]:
        """A user's characters, the default (first linked) first."""
        async with self.conn.execute(
            f"SELECT {self._LINK_COLUMNS} FROM links WHERE discord_id = ? ORDER BY linked_at, character_id",
            (discord_id,),
        ) as cur:
            return [Link(*row) for row in await cur.fetchall()]

    async def list_links(self) -> list[Link]:
        async with self.conn.execute(f"SELECT {self._LINK_COLUMNS} FROM links ORDER BY linked_at") as cur:
            return [Link(*row) for row in await cur.fetchall()]

    async def delete_link(self, discord_id: int, character_id: int) -> bool:
        """Remove one linked character, and its tracked data unless someone else still links it."""
        cur = await self.conn.execute(
            "DELETE FROM links WHERE discord_id = ? AND character_id = ?", (discord_id, character_id)
        )
        async with self.conn.execute("SELECT 1 FROM links WHERE character_id = ?", (character_id,)) as check:
            still_linked = await check.fetchone() is not None
        if not still_linked:
            for table in ("snapshots", "history", "announced"):
                await self.conn.execute(f"DELETE FROM {table} WHERE character_id = ?", (character_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------- snapshots ----------

    async def save_snapshot(self, discord_id: int, me: dict[str, Any]) -> None:
        """Store a character's latest profile data, and today's row of XP history."""
        character_id = me.get("id")
        if character_id is None:
            return
        skills = {
            str(sk["skill_key"]).lower(): [int(sk.get("level") or 0), int(sk.get("xp") or 0)]
            for sk in me.get("skills") or []
            if isinstance(sk, dict) and sk.get("skill_key")
        }
        clan = me.get("clan") if isinstance(me.get("clan"), dict) else {}
        updated_at = str(me.get("generated_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
        total_xp = int(me.get("total_xp") or sum(xp for _, xp in skills.values()))
        await self.conn.execute(
            """
            INSERT INTO snapshots (character_id, discord_id, username, clan_tag, skills, updated_at,
                                   total_level, total_xp, combat_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_id) DO UPDATE SET
                discord_id = excluded.discord_id,
                username = excluded.username,
                clan_tag = excluded.clan_tag,
                skills = excluded.skills,
                updated_at = excluded.updated_at,
                total_level = excluded.total_level,
                total_xp = excluded.total_xp,
                combat_level = excluded.combat_level
            """,
            (
                character_id, discord_id, str(me.get("username") or "?"), clan.get("tag"), json.dumps(skills),
                updated_at, me.get("total_level"), total_xp, me.get("combat_level"),
            ),
        )
        await self.conn.execute(
            """
            INSERT INTO history (character_id, day, total_xp, skills) VALUES (?, ?, ?, ?)
            ON CONFLICT(character_id, day) DO UPDATE SET total_xp = excluded.total_xp, skills = excluded.skills
            """,
            (character_id, datetime.now(timezone.utc).date().isoformat(), total_xp, json.dumps(skills)),
        )
        await self.conn.commit()

    _SNAPSHOT_COLUMNS = (
        "character_id, discord_id, username, clan_tag, skills, updated_at, total_level, total_xp, combat_level"
    )

    @staticmethod
    def _snapshot(row) -> Snapshot:
        return Snapshot(row[0], row[1], row[2], row[3], json.loads(row[4]), *row[5:])

    async def list_snapshots(self) -> list[Snapshot]:
        async with self.conn.execute(f"SELECT {self._SNAPSHOT_COLUMNS} FROM snapshots") as cur:
            return [self._snapshot(r) for r in await cur.fetchall()]

    async def get_snapshot(self, character_id: int) -> Snapshot | None:
        async with self.conn.execute(
            f"SELECT {self._SNAPSHOT_COLUMNS} FROM snapshots WHERE character_id = ?", (character_id,)
        ) as cur:
            row = await cur.fetchone()
        return self._snapshot(row) if row else None

    # ---------- XP history ----------

    async def history_baseline(self, character_id: int, on_or_before: str) -> HistoryRow | None:
        """The last history row on or before `on_or_before` (YYYY-MM-DD), else the earliest row."""
        queries = [
            ("SELECT day, total_xp, skills FROM history WHERE character_id = ? AND day <= ? "
             "ORDER BY day DESC LIMIT 1", (character_id, on_or_before)),
            ("SELECT day, total_xp, skills FROM history WHERE character_id = ? ORDER BY day ASC LIMIT 1",
             (character_id,)),
        ]
        for sql, params in queries:
            async with self.conn.execute(sql, params) as cur:
                row = await cur.fetchone()
            if row:
                return HistoryRow(row[0], row[1], json.loads(row[2]))
        return None

    async def prune_history(self, before_day: str) -> None:
        await self.conn.execute("DELETE FROM history WHERE day < ?", (before_day,))
        await self.conn.commit()

    # ---------- level-up announcements ----------

    async def get_announced(self) -> dict[int, dict[str, int]]:
        async with self.conn.execute("SELECT character_id, levels FROM announced") as cur:
            return {r[0]: json.loads(r[1]) for r in await cur.fetchall()}

    async def set_announced(self, character_id: int, levels: dict[str, int]) -> None:
        await self.conn.execute(
            "INSERT INTO announced (character_id, levels) VALUES (?, ?) "
            "ON CONFLICT(character_id) DO UPDATE SET levels = excluded.levels",
            (character_id, json.dumps(levels)),
        )
        await self.conn.commit()

    # ---------- per-server feature settings ----------

    _FEATURE_COLUMNS = "guild_id, feature, channel_id, role_id, status_message_id"

    async def get_feature(self, guild_id: int, feature: str) -> FeatureConfig | None:
        async with self.conn.execute(
            f"SELECT {self._FEATURE_COLUMNS} FROM guild_features WHERE guild_id = ? AND feature = ?",
            (guild_id, feature),
        ) as cur:
            row = await cur.fetchone()
        return FeatureConfig(*row) if row else None

    async def list_features(self, guild_id: int) -> list[FeatureConfig]:
        async with self.conn.execute(
            f"SELECT {self._FEATURE_COLUMNS} FROM guild_features WHERE guild_id = ?", (guild_id,)
        ) as cur:
            return [FeatureConfig(*row) for row in await cur.fetchall()]

    async def feature_targets(self, feature: str) -> list[FeatureConfig]:
        """Every server that has a channel set for `feature`."""
        async with self.conn.execute(
            f"SELECT {self._FEATURE_COLUMNS} FROM guild_features WHERE feature = ? AND channel_id IS NOT NULL",
            (feature,),
        ) as cur:
            return [FeatureConfig(*row) for row in await cur.fetchall()]

    async def set_feature_field(self, guild_id: int, feature: str, field: str, value: int | None) -> None:
        if field not in ("channel_id", "role_id", "status_message_id"):
            raise ValueError(field)
        await self.conn.execute(
            f"""
            INSERT INTO guild_features (guild_id, feature, {field}) VALUES (?, ?, ?)
            ON CONFLICT(guild_id, feature) DO UPDATE SET {field} = excluded.{field}
            """,
            (guild_id, feature, value),
        )
        await self.conn.commit()

    # ---------- tracker state ----------

    async def get_state(self, key: str) -> dict[str, Any] | None:
        async with self.conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def set_state(self, key: str, value: dict[str, Any]) -> None:
        await self.conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        await self.conn.commit()
