"""Today's plan: everything about the issue that code can decide without AI.

Date, issue number, weekday rotation, language, phrase theme, and "memory"
read from earlier issues (words to avoid, words to review, topics not to repeat).
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def today_in(tz_name: str) -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


def load_history(data_dir: Path, start: date, before: date) -> list[dict]:
    """Saved issues from `start` up to (not including) `before`, newest first."""
    issues = []
    for f in sorted(data_dir.glob("*.json"), reverse=True):
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if not start <= d < before:
            continue
        try:
            issues.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return issues


def _since(history: list[dict], day: date, days: int) -> list[dict]:
    """Issues published within `days` days before `day`."""
    cutoff = day - timedelta(days=days)
    return [h for h in history if date.fromisoformat(h["plan"]["date"]) >= cutoff]


def _clean(items) -> list[str]:
    return [x for x in items if x]


def make_plan(cfg: dict, day: date, data_dir: Path) -> dict:
    start = date.fromisoformat(str(cfg["start_date"]))
    issue_no = (day - start).days + 1
    if issue_no < 1:
        raise SystemExit(f"{day} is before the first issue ({start}).")

    weekday = WEEKDAYS[day.weekday()]
    languages = cfg["languages"]
    language = languages[(issue_no - 1) % len(languages)]
    themes = cfg.get("phrase_themes") or ["everyday life"]
    phrase_theme = themes[((issue_no - 1) // len(languages)) % len(themes)]

    history = load_history(data_dir, start, day)
    by_date = {h["plan"]["date"]: h for h in history}

    # ★ words taught recently must not be taught again
    avoid = {w for h in _since(history, day, cfg.get("avoid_repeat_days", 30))
             for w in h["content"].get("star_words", [])}

    # ★ words from 1, 3, 7, 21 days ago come back as ↺ review candidates
    review_pool, seen = [], set()
    for n in cfg.get("review_days", [1, 3, 7, 21]):
        old = by_date.get((day - timedelta(days=n)).isoformat())
        for w in (old or {}).get("content", {}).get("star_words", []):
            if w.lower() not in seen:
                seen.add(w.lower())
                review_pool.append({"word": w, "taught_date": old["plan"]["date"], "days_ago": n})

    # topics of the last 4 weeks, so the AI does not repeat itself
    last4w = _since(history, day, 28)
    recent_traps = [h["content"].get("trap", {}).get("title") for h in last4w]
    recent_real = [h["content"].get("two_takes", {}).get("question") or
                   h["content"].get("say_it_better", {}).get("situation_title") for h in last4w]
    recent_features = [h["content"].get("feature", {}).get("headline") for h in last4w]

    # Other Languages: root / borrowed / curious words already shown for this language
    recent_lang = []
    for h in _since(history, day, 120):
        if h["plan"]["language"] == language:
            ol = h["content"].get("other_language", {})
            recent_lang += [ol.get("root", {}).get("root"), ol.get("borrowed", {}).get("word"),
                            ol.get("curious", {}).get("word")]

    # news used in the last few days: today's stories must be different
    window = cfg["news"].get("repeat_window_days", 3)
    recent_news = [s.get("title") for h in _since(history, day, window) for s in h.get("sources", [])]

    return {
        "date": day.isoformat(),
        "date_long": f"{day.strftime('%A')}, {day.day} {day.strftime('%B %Y')}",
        "weekday": weekday,
        "issue_no": issue_no,
        "language": language,
        "phrase_theme": phrase_theme,
        **cfg["rotation"][weekday],          # feature, opinion, setting, activity, trap
        "avoid_star_words": sorted(avoid, key=str.lower),
        "review_pool": review_pool,
        "recent_traps": _clean(recent_traps),
        "recent_opinions": _clean(recent_real),
        "recent_features": _clean(recent_features),
        "recent_language_notes": _clean(recent_lang),
        "recent_news_titles": _clean(recent_news),
    }
