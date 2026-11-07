"""Koruxa wiki cache: an honest, polite crawler + parser for item pages.

Legitimacy: robots.txt grants User-agent:* `Allow: /` with search=yes/use=reference
(we build a lookup index, not AI training). We identify honestly (no browser
spoofing), send conditional requests (etag/last-modified) and rate-limit, mirroring
the reference bot. Item URLs come from the wiki sitemap, so no blind crawling.

Parsing extracts structured factual data (category, rarity, level, prices, stats,
recipes, drops) from each item's <table class="wtab"> blocks.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import re
import time
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

BASE = "https://koruxa.com"
SITEMAP = "https://koruxa.com/wiki/sitemap.xml"
USER_AGENT = "KoruxaDiscordBot/1.0 (+https://github.com/Wonder-Gaming/Koruxa-Bot; wiki lookup cache; honest crawler)"
REQUEST_DELAY = 0.65
ITEM_RE = re.compile(r"/wiki/items/([a-z0-9_]+)\.html", re.I)

_TAG = re.compile(r"<[^>]+>")
_CELL = re.compile(r"<(th|td)\b[^>]*>(.*?)</\1>", re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.I | re.S)
_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)
_NUM = re.compile(r"([0-9][0-9,]*)")


def _text(raw: str) -> str:
    return html_mod.unescape(_TAG.sub(" ", raw)).replace("\xa0", " ").strip()


def _clean(raw: str) -> str:
    return re.sub(r"\s+", " ", _text(raw)).strip()


def _int(raw: str) -> int | None:
    m = _NUM.search(raw or "")
    return int(m.group(1).replace(",", "")) if m else None


def _rows(table_html: str) -> list[list[tuple[str, str]]]:
    rows = []
    for row in _ROW.findall(table_html):
        cells = [(tag.lower(), _clean(body)) for tag, body in _CELL.findall(row)]
        if cells:
            rows.append(cells)
    return rows


def parse_item_html(page: str, item_key: str = "", url: str = "") -> dict[str, Any]:
    name_m = _H1.search(page)
    item: dict[str, Any] = {
        "item_key": item_key,
        "name": _clean(name_m.group(1)) if name_m else item_key,
        "url": url,
        "recipes": [], "drops": [], "thieving": [], "stats": {},
    }
    for table_html in _TABLE.findall(page):
        rows = _rows(table_html)
        if not rows:
            continue
        # Infobox: rows of [th label, td value]
        if all(len(r) == 2 and r[0][0] == "th" for r in rows):
            for (_, label), (_, value) in rows:
                _apply_infobox(item, label, value)
            continue
        # Grid table: first row all <th> headers, rest data
        header = [c[1] for c in rows[0]]
        data = [[c[1] for c in r] for r in rows[1:]]
        _apply_grid(item, header, data)
    return item


def _apply_infobox(item: dict[str, Any], label: str, value: str) -> None:
    key = label.strip().lower()
    if key == "category":
        item["category"] = value
    elif key == "rarity":
        item["rarity"] = value
    elif key == "required level":
        item["required_level"] = _int(value)
    elif key == "equip slot":
        item["equip_slot"] = value
    elif key == "weapon type":
        item["weapon_type"] = value
    elif key == "sell price":
        item["npc_sell"] = _int(value)
    elif key == "market price":
        item["market_price"] = _int(value)
        item["market_approx"] = value.strip().startswith("~")
        vol = re.search(r"([0-9,]+)\s+traded", value)
        item["market_volume_7d"] = int(vol.group(1).replace(",", "")) if vol else None
    elif key in ("heals", "heal"):
        item["heal"] = _int(value)
    elif key == "stats":
        if value and value != "—":
            item["stats_text"] = value


def _apply_grid(item: dict[str, Any], header: list[str], data: list[list[str]]) -> None:
    h0 = (header[0] if header else "").strip().lower()
    cols = [c.strip().lower() for c in header]
    if h0 == "skill":  # recipe: Skill, Recipe, Lv
        for row in data:
            r = dict(zip(cols, row))
            item["recipes"].append({"skill": r.get("skill", ""), "recipe": r.get("recipe", ""), "level": r.get("lv", "")})
    elif h0.startswith("dropped by"):  # drops: Dropped by, Qty, Chance
        for row in data:
            r = dict(zip(cols, row))
            item["drops"].append({"source": r.get("dropped by", ""), "qty": r.get("qty", ""), "chance": r.get("chance", "")})
    elif h0.startswith("stolen from"):  # thieving source
        for row in data:
            r = dict(zip(cols, row))
            item["thieving"].append({"source": row[0] if row else "", "qty": r.get("qty", ""), "chance": r.get("chance", "")})
    # Other grids (e.g. the enchant +N progression) are ignored; base stats come
    # from the infobox "Stats" field.


class WikiCache:
    """Stores parsed items in a wiki_items table (uses the bot's sqlite connection)."""

    def __init__(self, db):
        self.db = db
        self.session: aiohttp.ClientSession | None = None
        self.syncing = False
        self.status = {"total": 0, "done": 0, "started_at": None, "finished_at": None}

    async def setup(self) -> None:
        await self.db.conn.execute(
            """CREATE TABLE IF NOT EXISTS wiki_items (
                 item_key TEXT PRIMARY KEY, name TEXT, name_norm TEXT,
                 data_json TEXT, etag TEXT, last_modified TEXT, fetched_at REAL)"""
        )
        await self.db.conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_name ON wiki_items(name_norm)")
        await self.db.conn.commit()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers={"user-agent": USER_AGENT})
        return self.session

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def item_urls(self) -> list[tuple[str, str]]:
        """(item_key, url) for every item in the wiki sitemap."""
        session = await self._ensure_session()
        async with session.get(SITEMAP, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            xml = await resp.text()
        out, seen = [], set()
        for m in ITEM_RE.finditer(xml):
            key = m.group(1).lower()
            if key not in seen:
                seen.add(key)
                out.append((key, f"{BASE}/wiki/items/{key}.html"))
        return out

    async def count(self) -> int:
        async with self.db.conn.execute("SELECT COUNT(*) FROM wiki_items") as cur:
            return (await cur.fetchone())[0]

    async def get(self, item_key: str) -> dict[str, Any] | None:
        async with self.db.conn.execute("SELECT data_json FROM wiki_items WHERE item_key=?", (item_key,)) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def search(self, query: str, limit: int = 25) -> list[dict[str, str]]:
        needle = re.sub(r"\s+", " ", query.strip().lower())
        if not needle:
            return []
        like = f"%{needle}%"
        async with self.db.conn.execute(
            "SELECT item_key, name FROM wiki_items WHERE name_norm LIKE ? ORDER BY length(name) LIMIT ?",
            (like, limit)) as cur:
            return [{"item_key": k, "name": n} for k, n in await cur.fetchall()]

    async def _store(self, key: str, item: dict, etag: str | None, last_mod: str | None) -> None:
        await self.db.conn.execute(
            """INSERT INTO wiki_items(item_key,name,name_norm,data_json,etag,last_modified,fetched_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(item_key) DO UPDATE SET name=excluded.name, name_norm=excluded.name_norm,
                 data_json=excluded.data_json, etag=excluded.etag, last_modified=excluded.last_modified,
                 fetched_at=excluded.fetched_at""",
            (key, item["name"], item["name"].lower(), json.dumps(item), etag, last_mod, time.time()))

    async def refresh_one(self, key: str) -> dict[str, Any] | None:
        """Fetch + cache a single item page on demand."""
        session = await self._ensure_session()
        url = f"{BASE}/wiki/items/{key}.html"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return None
                item = parse_item_html(await resp.text(), key, url)
                await self._store(key, item, resp.headers.get("etag"), resp.headers.get("last-modified"))
                await self.db.conn.commit()
                return item
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("wiki on-demand fetch failed for %s: %s", key, e)
            return None

    async def sync(self, *, force: bool = False) -> dict[str, int]:
        """Full crawl of every sitemap item (honest UA, polite delay, conditional GET)."""
        if self.syncing:
            return {"skipped": 1}
        self.syncing = True
        fetched = updated = not_modified = errors = 0
        try:
            session = await self._ensure_session()
            urls = await self.item_urls()
            self.status = {"total": len(urls), "done": 0, "started_at": time.time(), "finished_at": None}
            for key, url in urls:
                meta = None
                if not force:
                    async with self.db.conn.execute(
                            "SELECT etag, last_modified FROM wiki_items WHERE item_key=?", (key,)) as cur:
                        meta = await cur.fetchone()
                headers = {}
                if meta and meta[0]:
                    headers["if-none-match"] = meta[0]
                if meta and meta[1]:
                    headers["if-modified-since"] = meta[1]
                try:
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        if resp.status == 304:
                            not_modified += 1
                        elif resp.status == 200:
                            page = await resp.text()
                            item = parse_item_html(page, key, url)
                            await self._store(key, item, resp.headers.get("etag"), resp.headers.get("last-modified"))
                            fetched += 1
                            updated += 1
                        else:
                            errors += 1
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    errors += 1
                    log.warning("wiki fetch failed for %s: %s", key, e)
                self.status["done"] += 1
                if self.status["done"] % 100 == 0:
                    await self.db.conn.commit()
                await asyncio.sleep(REQUEST_DELAY)
            await self.db.conn.commit()
        finally:
            self.syncing = False
            self.status["finished_at"] = time.time()
        log.info("wiki sync done: %d fetched, %d not-modified, %d errors", fetched, not_modified, errors)
        return {"fetched": fetched, "updated": updated, "not_modified": not_modified, "errors": errors}
