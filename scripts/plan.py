"""Work out everything about today's issue that does not need AI:
date, issue number, weekday rotation, language, and vocabulary history."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def today_in(tz_name: str) -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


def load_history(data_dir: Path, before: date) -> list[dict]:
    """Saved issues older than `before`, newest first."""
    issues = []
    for f in sorted(data_dir.glob("*.json"), reverse=True):
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d >= before:
            continue
        try:
            issues.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return issues


def make_plan(cfg: dict, day: date, data_dir: Path) -> dict:
    start = date.fromisoformat(str(cfg["start_date"]))
    issue_no = (day - start).days + 1
    if issue_no < 1:
        raise SystemExit(f"{day} is before the first issue ({start}).")

    weekday = WEEKDAYS[day.weekday()]
    rot = dict(cfg["rotation"][weekday])
    languages = cfg["languages"]
    language = languages[(issue_no - 1) % len(languages)]

    history = load_history(data_dir, day)
    by_date = {h["plan"]["date"]: h for h in history}

    avoid_cutoff = day - timedelta(days=cfg.get("avoid_repeat_days", 30))
    recent = [h for h in history if date.fromisoformat(h["plan"]["date"]) >= avoid_cutoff]

    avoid_star = sorted({w for h in recent for w in h["content"].get("star_words", [])}, key=str.lower)

    review_pool = []
    for n in cfg.get("review_days", [1, 3, 7, 21]):
        old = by_date.get((day - timedelta(days=n)).isoformat())
        if not old:
            continue
        for w in old["content"].get("star_words", []):
            if w.lower() not in {r["word"].lower() for r in review_pool}:
                review_pool.append({"word": w, "taught_issue": old["plan"]["issue_no"],
                                    "taught_date": old["plan"]["date"], "days_ago": n})

    last4w = [h for h in history if date.fromisoformat(h["plan"]["date"]) >= day - timedelta(days=28)]
    recent_traps = [(h["content"].get("trap") or {}).get("title", "") for h in last4w]
    recent_opinions = []
    for h in last4w:
        c = h["content"]
        if c.get("two_takes"):
            recent_opinions.append(c["two_takes"].get("question", ""))
        if c.get("say_it_better"):
            recent_opinions.append(c["say_it_better"].get("situation_title", ""))
    recent_features = [(h["content"].get(k) or {}).get("headline", "")
                       for h in last4w for k in ("feature", "feature_a", "feature_b")]

    # Other Languages: words already shown for this language (last 120 days)
    recent_lang = []
    for h in history:
        if h["plan"].get("language") != language or \
                date.fromisoformat(h["plan"]["date"]) < day - timedelta(days=120):
            continue
        ol = h["content"].get("other_language") or {}
        for key in ("root", "borrowed", "curious"):
            item = ol.get(key) or {}
            recent_lang.append(item.get("root") or item.get("word") or "")
    # news covered in the last 3 issues: today's stories must be different
    recent_news = [src.get("title", "") for h in history
                   if date.fromisoformat(h["plan"]["date"]) >= day - timedelta(days=3)
                   for src in h.get("sources", [])]
    themes = cfg.get("phrase_themes") or ["everyday life"]
    phrase_theme = themes[((issue_no - 1) // len(languages)) % len(themes)]

    return {
        "date": day.isoformat(),
        "date_long": f"{day.strftime('%A')}, {day.day} {day.strftime('%B %Y')}",
        "weekday": weekday,
        "issue_no": issue_no,
        "language": language,
        "phrase_theme": phrase_theme,
        **rot,
        "avoid_star_words": avoid_star,
        "review_pool": review_pool,
        "recent_traps": [t for t in recent_traps if t],
        "recent_opinions": [t for t in recent_opinions if t],
        "recent_language_notes": [t for t in recent_lang if t],
        "recent_features": [t for t in recent_features if t],
        "recent_news_titles": [t for t in recent_news if t],
    }
