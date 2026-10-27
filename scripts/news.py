"""Collect real news from RSS feeds, group stories covered by several outlets,
and download article text for the stories the editor picks."""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

UA = "Mozilla/5.0 (compatible; BKKGemPostBot/1.0; +https://github.com)"
STOP = set("""a an the and or but of to in on at for from by with as is are was were be been
has have had will would can could may might this that these those it its after before over
into about than new says say said amid near up out off more most not no""".split())


def _clean(text: str, limit_words: int = 60) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    words = text.split()
    return " ".join(words[:limit_words]) + (" …" if len(words) > limit_words else "")


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOP and len(w) > 2}


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def fetch_feed(url: str) -> list:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"not a valid feed: {parsed.bozo_exception}")
    return parsed.entries


def collect(cfg: dict, log=print) -> dict:
    """Return {"world": [...], "thailand": [...]} candidate stories."""
    ncfg = cfg["news"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ncfg.get("max_age_hours", 36))
    pools = {}
    for pool, feeds in ncfg["feeds"].items():
        items, seen = [], set()
        for feed in feeds:
            try:
                entries = fetch_feed(feed["url"])
            except Exception as e:  # noqa: BLE001 - any feed problem is non-fatal
                log(f"  ! feed skipped: {feed['name']} ({e.__class__.__name__}: {e})")
                continue
            kept = 0
            for e in entries:
                title = _clean(e.get("title", ""), 30)
                link = e.get("link", "")
                if not title or not link:
                    continue
                key = re.sub(r"\W+", "", title.lower())
                if key in seen:
                    continue
                pub = _published(e)
                if pub and pub < cutoff:
                    continue
                seen.add(key)
                items.append({
                    "pool": pool, "source": feed["name"], "title": title,
                    "summary": _clean(e.get("summary", "")), "link": link,
                    "published": pub.isoformat() if pub else None,
                })
                kept += 1
            log(f"  {feed['name']}: {kept} recent items")
        pools[pool] = items

    for pool, items in pools.items():
        for it in items:
            tk = _tokens(it["title"])
            it["also"] = []
            for other in items:
                if other is it or other["source"] == it["source"]:
                    continue
                ok = _tokens(other["title"])
                if tk and ok and len(tk & ok) / len(tk | ok) >= 0.3:
                    it["also"].append(other)
    for pool, items in pools.items():
        # corroborated first, then newest
        items.sort(key=lambda x: (len({o["source"] for o in x["also"]}), x["published"] or ""), reverse=True)
        limit = ncfg.get("max_candidates", {}).get(pool, 30)
        pools[pool] = items[:limit]
        prefix = "W" if pool == "world" else "T"
        for i, it in enumerate(pools[pool], 1):
            it["id"] = f"{prefix}{i}"
    # keep only plain data: other outlets as {source, title, link}
    for items in pools.values():
        for it in items:
            it["also"] = [{"source": o["source"], "title": o["title"], "link": o["link"],
                           "summary": o["summary"]} for o in it["also"]]
    return pools


def candidates_text(pools: dict) -> str:
    lines = []
    for pool, items in pools.items():
        lines.append(f"## {pool.upper()} CANDIDATES")
        for it in items:
            also = sorted({o["source"] for o in it["also"]})
            extra = f" | also reported by: {', '.join(also)}" if also else ""
            lines.append(f"{it['id']} | {it['source']} | {it['title']} | {it['summary']}{extra}")
        lines.append("")
    return "\n".join(lines)


def article_text(url: str, fallback: str, max_words: int = 1100) -> str:
    try:
        import trafilatura
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
    except Exception:  # noqa: BLE001
        text = ""
    words = text.split()
    if len(words) < 80:  # paywall, block page or empty: fall back to the feed summary
        return fallback
    return " ".join(words[:max_words])


def gather_sources(pools: dict, picks: dict, log=print) -> list[dict]:
    """Download text for picked stories. Thai stories get one extra outlet when available."""
    index = {it["id"]: it for items in pools.values() for it in items}
    sources = []

    def add(item, role):
        sid = f"S{len(sources) + 1}"
        text = article_text(item["link"], f"{item['title']}. {item['summary']}")
        sources.append({"id": sid, "role": role, "source": item["source"], "title": item["title"],
                        "url": item["link"], "text": text})
        log(f"  {sid} {role}: {item['source']} — {item['title'][:70]} ({len(text.split())} words)")
        return sid

    roles = [("cover", picks["cover"])] + \
        [(f"world_{n}", i) for n, i in enumerate(picks["world"], 1)] + \
        [(f"thailand_{n}", i) for n, i in enumerate(picks["thailand"], 1)]
    for role, pid in roles:
        item = index[pid]
        add(item, role)
        if item["pool"] == "thailand" and item["also"]:
            add(item["also"][0], role + " (second source)")
    return sources


def load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
