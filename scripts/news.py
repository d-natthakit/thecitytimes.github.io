"""News without AI: read RSS feeds, rank stories, download article text.

1. collect()        read every feed, drop old / unsuitable items, group stories
                    that several outlets report, rank them
2. select_sources() pick the cover, 2 world and 2 Thai stories and download
                    their text (skipping recent repeats and paywalled pages)
"""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

UA = "Mozilla/5.0 (compatible; CityTimesBot/1.0; +https://github.com)"

# Words ignored when comparing headlines
STOP = set("""a an the and or but of to in on at for from by with as is are was were be been
has have had will would can could may might this that these those it its after before over
into about than new says say said amid near up out off more most not no""".split())

# Headlines that are not real news stories, and topics the paper avoids in Thai news
SKIP_ALL = re.compile(r"^(live|watch|video|opinion|podcast|quiz)\b|\blive updates\b|\bas it happened\b|"
                      r"\bin pictures\b|\bcrossword\b|\bnewsletter\b", re.I)
SKIP_THAI = re.compile(r"\b(king|queen|royal|monarch\w*|prince|princess|palace|lese|majeste|section 112)\b", re.I)

MIN_WORDS = {"cover": 350, "world": 120, "thailand": 120}   # source text needed to write the article
LIMIT = {"cover": 900, "world": 450, "thailand": 450}       # max words sent to the AI (saves tokens)
SECOND_SOURCE_LIMIT = 300


# ---------- small helpers ----------

def _clean(text: str, limit_words: int = 60) -> str:
    """Strip HTML from feed text and cut it to a few words."""
    words = re.sub(r"<[^>]+>", " ", html.unescape(text or "")).split()
    return " ".join(words[:limit_words]) + (" …" if len(words) > limit_words else "")


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOP and len(w) > 2}


def similar(a: str, b: str) -> bool:
    """Two headlines are about the same story if they share ≥30% of their key words."""
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.3


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


def article_text(url: str, fallback: str, max_words: int) -> str:
    """Main text of a news page, or the feed summary if the page is blocked/paywalled."""
    try:
        import trafilatura
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
    except Exception:  # noqa: BLE001 - any download problem means "use the summary"
        text = ""
    words = text.split()
    return " ".join(words[:max_words]) if len(words) >= 80 else fallback


# ---------- 1. collect ----------

def collect(cfg: dict, log=print) -> dict:
    """Return {"world": [...], "thailand": [...]}: ranked candidate stories."""
    ncfg = cfg["news"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ncfg.get("max_age_hours", 36))
    pools = {}
    for pool, feeds in ncfg["feeds"].items():
        items, seen = [], set()
        for feed in feeds:
            try:
                entries = fetch_feed(feed["url"])
            except Exception as e:  # noqa: BLE001 - a broken feed must not stop the issue
                log(f"  ! feed skipped: {feed['name']} ({e.__class__.__name__}: {e})")
                continue
            kept = 0
            for e in entries:
                title, link = _clean(e.get("title", ""), 30), e.get("link", "")
                key = re.sub(r"\W+", "", title.lower())
                pub = _published(e)
                if not title or not link or key in seen or blocked(pool, title) or (pub and pub < cutoff):
                    continue
                seen.add(key)
                items.append({"pool": pool, "source": feed["name"], "title": title, "link": link,
                              "summary": _clean(e.get("summary", "")),
                              "published": pub.isoformat() if pub else None})
                kept += 1
            log(f"  {feed['name']}: {kept} recent items")

        # other outlets reporting the same story
        for it in items:
            it["also"] = [{k: o[k] for k in ("source", "title", "link", "summary")}
                          for o in items if o["source"] != it["source"] and similar(it["title"], o["title"])]

        # rank: reported by more outlets first, then newest
        items.sort(key=lambda x: (len({o["source"] for o in x["also"]}), x["published"] or ""), reverse=True)
        items = items[:ncfg.get("max_candidates", {}).get(pool, 30)]
        for i, it in enumerate(items, 1):
            it["id"] = f"{pool[0].upper()}{i}"
        pools[pool] = items
    return pools


# ---------- 2. select stories and download their text ----------

def select_sources(pools: dict, recent_titles=(), log=print, max_tries: int = 4) -> tuple[dict, list[dict]]:
    """Pick today's stories and download their text.

    - ranking from collect(); no two stories about the same event
    - skips stories similar to ones used in the last few days
    - if a page gives too little text (paywall/block), tries the next story;
      if none is better, the thinnest story is marked "thin" so the AI writes it shorter
    - cover and Thai stories also get a second outlet when one exists
    Returns (picks, sources). Each source: id, role, source, title, url, text, thin.
    """
    chosen: list[dict] = []
    sources: list[dict] = []
    cache: dict[str, str] = {}

    def fresh(it, allow_repeat=False) -> bool:
        return it not in chosen and \
            (allow_repeat or not any(similar(it["title"], t) for t in recent_titles)) and \
            not any(similar(it["title"], c["title"]) for c in chosen)

    def fetch(item, limit) -> str:
        if item["link"] not in cache:
            cache[item["link"]] = article_text(item["link"], f"{item['title']}. {item['summary']}", limit)
        return cache[item["link"]]

    def fill(role: str, items: list, kind: str, allow_repeat=False) -> str:
        best, tries = None, 0
        # stable sort keeps the ranking but prefers outlets not used yet today
        for it in sorted(items, key=lambda x: any(x["source"] == c["source"] for c in chosen)):
            if not fresh(it, allow_repeat):
                continue
            tries += 1
            parts = [(it, fetch(it, LIMIT[kind]))]
            words = len(parts[0][1].split())
            if it["also"] and (kind != "world" or words < MIN_WORDS[kind]):
                parts.append((it["also"][0], fetch(it["also"][0], SECOND_SOURCE_LIMIT)))
                words += len(parts[1][1].split())
            if best is None or words > best[0]:
                best = (words, it, parts)
            if words >= MIN_WORDS[kind] or tries >= max_tries:
                break
            log(f"  · {role}: only {words} words of source text for “{it['title'][:50]}”, trying another story")
        if not best:
            if allow_repeat:
                raise ValueError(f"no usable story for {role}")
            # few feeds today: better a follow-up on a recent story than no issue
            log(f"  · {role}: no new story left, allowing a story covered in the last few days")
            return fill(role, items, kind, allow_repeat=True)

        words, it, parts = best
        chosen.append(it)
        for n, (item, text) in enumerate(parts):
            sid = f"S{len(sources) + 1}"
            label = role if n == 0 else f"{role} (second source)"
            sources.append({"id": sid, "role": label, "source": item["source"], "title": item["title"],
                            "url": item["link"], "text": text, "thin": words < MIN_WORDS[kind]})
            log(f"  {sid} {label}: {item['source']} — {item['title'][:70]} ({len(text.split())} words)")
        return it["id"]

    def reach(items) -> int:
        """How many other outlets report the best fresh story of a pool."""
        first = next((it for it in items if fresh(it)), None)
        return len({o["source"] for o in first["also"]}) if first else -1

    world, thai = pools["world"], pools["thailand"]
    cover_pool = thai if reach(thai) > reach(world) else world
    picks = {"cover": fill("cover", cover_pool, "cover")}
    picks["world"] = [fill(f"world_{n}", world, "world") for n in (1, 2)]
    picks["thailand"] = [fill(f"thailand_{n}", thai, "thailand") for n in (1, 2)]
    return picks, sources


def load_fixture(path: Path) -> dict:
    """Offline test news (tests/fixtures/news.json)."""
    return json.loads(path.read_text(encoding="utf-8"))
