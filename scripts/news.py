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


SKIP_ALL = re.compile(r"^(live|watch|video|opinion|podcast|quiz)\b|\blive updates\b|\bas it happened\b|"
                      r"\bin pictures\b|\bcrossword\b|\bnewsletter\b", re.I)
SKIP_THAI = re.compile(r"\b(king|queen|royal|monarch\w*|prince|princess|palace|lese|majeste|section 112)\b", re.I)


def blocked(pool: str, title: str) -> bool:
    return bool(SKIP_ALL.search(title) or (pool == "thailand" and SKIP_THAI.search(title)))


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
                if key in seen or blocked(pool, title):
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


def _similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.3


MIN_WORDS = {"cover": 350, "world": 120, "thailand": 120}   # source text needed to write the article
LIMIT = {"cover": 900, "world": 450, "thailand": 450}       # max words sent to the AI (saves tokens)


def select_sources(pools: dict, recent_titles=(), log=print, max_tries: int = 4) -> tuple[dict, list[dict]]:
    """Choose today's stories without AI and download their text.

    - widely reported first, then newest; no near-duplicates
    - skips stories already covered in the last few issues
    - if a page is paywalled or blocked (too little text), tries the next story
    - cover and Thai stories also get a second outlet when one exists
    """
    chosen: list[dict] = []
    sources: list[dict] = []

    def fresh(it):
        return it not in chosen and \
            not any(_similar(it["title"], t) for t in recent_titles) and \
            not any(_similar(it["title"], c["title"]) for c in chosen)

    cache: dict[str, str] = {}

    def fetch(item, limit):
        if item["link"] not in cache:
            cache[item["link"]] = article_text(item["link"], f"{item['title']}. {item['summary']}", limit)
        return cache[item["link"]]

    def fill(role: str, items: list, kind: str) -> str:
        best, tries = None, 0
        # stable sort: keeps the ranking, but prefers outlets not used yet
        for it in sorted(items, key=lambda x: any(x["source"] == c["source"] for c in chosen)):
            if not fresh(it):
                continue
            tries += 1
            parts = [(it, fetch(it, LIMIT[kind]))]
            words = len(parts[0][1].split())
            if it["also"] and (kind != "world" or words < MIN_WORDS[kind]):
                second = it["also"][0]
                parts.append((second, fetch(second, 300)))
                words += len(parts[1][1].split())
            if best is None or words > best[0]:
                best = (words, it, parts)
            if words >= MIN_WORDS[kind]:
                break
            log(f"  · {role}: only {words} words of source text for “{it['title'][:50]}”, trying another story")
            if tries >= max_tries:
                break
        if not best:
            raise ValueError(f"no usable story for {role}")
        words, it, parts = best
        chosen.append(it)
        for n, (item, text) in enumerate(parts):
            sid = f"S{len(sources) + 1}"
            r = role if n == 0 else role + " (second source)"
            sources.append({"id": sid, "role": r, "source": item["source"], "title": item["title"],
                            "url": item["link"], "text": text, "thin": words < MIN_WORDS[kind]})
            log(f"  {sid} {r}: {item['source']} — {item['title'][:70]} ({len(text.split())} words)")
        return it["id"]

    def reach(items):
        first = next((it for it in items if fresh(it)), None)
        return len({o["source"] for o in first["also"]}) if first else -1

    w, t = pools["world"], pools["thailand"]
    cover_from_thai = reach(t) > reach(w)
    picks = {"cover": fill("cover", t if cover_from_thai else w, "cover")}
    picks["world"] = [fill(f"world_{n}", w, "world") for n in (1, 2)]
    picks["thailand"] = [fill(f"thailand_{n}", t, "thailand") for n in (1, 2)]
    return picks, sources


def load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
