"""Quality checks.

Only CRITICAL problems are sent back to the AI (one repair per step).
Everything else is fixed by autofix.py or reported as a note."""
import re

THAI = re.compile(r"[฀-๿]")
CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")
STAR = re.compile(r"\*\*(.+?)\*\*")
MARKUP = re.compile(r"\*\*|__(?=[^_\s])|(?<=[^_\s])__")

RANGES = {  # body words, from the editorial plan
    "cover": (430, 470), "world": (140, 160), "thailand": (140, 160),
    "feature_a": (280, 320), "feature_b": (280, 320), "opinion": (260, 300),
    "language_lab": (380, 420), "other_language": (150, 180),
}
TOTAL = (2800, 3000)
CRITICAL_LOW, CRITICAL_HIGH = 0.6, 1.5      # only far-off lengths are worth a repair


def plain(text: str) -> str:
    return MARKUP.sub("", text or "")


def nwords(text: str) -> int:
    return len(plain(text).split())


def word_pattern(word: str) -> re.Pattern:
    parts = []
    for tok in re.findall(r"[A-Za-z]+", word.lower()):
        if len(tok) >= 4 and tok[-1] in "ey":
            tok = tok[:-1]
        parts.append(re.escape(tok) + (r"\w*" if len(tok) >= 3 else ""))
    return re.compile(r"\b" + r"[\s-]+".join(parts) if parts else r"$^", re.I)


def strings(obj, path=""):
    """Yield (path, text) for every string in the content."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from strings(v, f"{path}[{i}]")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k != "source_ids":
                yield from strings(v, f"{path}.{k}" if path else k)


def article_blocks(c: dict) -> dict[str, str]:
    def art(a):
        return a["headline"] + "\n" + "\n".join(a["paragraphs"])
    blocks = {"cover": art(c["cover"]), "feature_a": art(c["feature_a"]), "feature_b": art(c["feature_b"])}
    for i, a in enumerate(c["world"], 1):
        blocks[f"world_{i}"] = art(a)
    for i, a in enumerate(c["thailand"], 1):
        blocks[f"thailand_{i}"] = art(a)
    if c.get("two_takes"):
        t = c["two_takes"]
        blocks["opinion"] = "\n".join([t["question"], t["view_a"]["text"], t["view_b"]["text"]])
    elif c.get("say_it_better"):
        s = c["say_it_better"]
        blocks["opinion"] = "\n".join([s["situation"], s["formal"], s["neutral"], s["casual"]])
    return blocks


def lab_text(c: dict) -> str:
    parts = []
    for w in c.get("word_workshop", []):
        parts += [w["word"], *w["collocations"], w["example"]]
    act = c.get("activity", {})
    parts += [act.get("instructions", ""), *act.get("items", []), *act.get("answers", [])]
    for e in c.get("errors", []):
        parts += [e["sentence"], e["corrected"]]
    return plain("\n".join(parts))


def section_words(c: dict) -> dict[str, int]:
    out = {"cover": sum(nwords(p) for p in c["cover"]["paragraphs"])}
    for key in ("world", "thailand"):
        for i, a in enumerate(c[key], 1):
            out[f"{key}_{i}"] = sum(nwords(p) for p in a["paragraphs"])
    for key in ("feature_a", "feature_b"):
        out[key] = sum(nwords(p) for p in c[key]["paragraphs"])
    op = c.get("two_takes") or c.get("say_it_better") or {}
    out["opinion"] = sum(nwords(t) for _, t in strings(op))
    if "word_workshop" in c:
        lab = {k: c[k] for k in ("word_workshop", "errors", "trap")}
        lab["activity"] = {k: c["activity"][k] for k in ("instructions", "items")}
        out["language_lab"] = sum(nwords(t) for _, t in strings(lab))
        out["other_language"] = sum(nwords(t) for _, t in strings(c["other_language"]))
        out["glossary"] = sum(nwords(t) for _, t in strings(c["glossary"]))
    out["total"] = sum(nwords(t) for _, t in strings(c))
    return out


def _length(sizes: dict, names: list[str], critical: list, notes: list):
    for name in names:
        if name not in sizes:
            continue
        key = name.split("_")[0] if name.startswith(("world", "thailand")) else name
        lo, hi = RANGES[key]
        n = sizes[name]
        if not (lo * CRITICAL_LOW <= n <= hi * CRITICAL_HIGH):
            critical.append(f"{name} has {n} words; it must be {lo}–{hi}")
        elif not (lo * 0.9 <= n <= hi * 1.1):
            notes.append(f"{name}: {n} words (target {lo}–{hi})")


def _scripts(c: dict, keys: tuple, critical: list, notes: list):
    for path, text in strings({k: c[k] for k in keys if k in c}):
        if THAI.search(text) and not (path.startswith("glossary") and path.endswith(".thai")) \
                and path != "trap.explanation":
            critical.append(f"Thai text is not allowed in {path}; write it in English")
        if CJK.search(text) and not path.startswith("other_language"):
            critical.append(f"Chinese/Japanese characters are not allowed in {path}")


def check_articles(c: dict, plan: dict, sources: list[dict]) -> tuple[list[str], list[str]]:
    critical, notes = [], []
    try:
        if len(c["world"]) != 2:
            critical.append("world must have exactly 2 articles")
        if len(c["thailand"]) != 2:
            critical.append("thailand must have exactly 2 articles")
        if not c.get(plan["opinion"]):
            critical.append(f"the opinion slot today must be {plan['opinion']}")
        sizes = section_words(c)
    except (KeyError, TypeError, IndexError) as e:
        return [f"JSON structure is wrong or incomplete (missing {e})"], notes

    if len(c["star_words"]) < 6:
        critical.append("fewer than 6 ★ words could be found in the articles; use 8 ★ words in the article text")
    avoid = {w.lower() for w in plan.get("avoid_star_words", [])}
    reused = [w for w in c["star_words"] if w.lower() in avoid]
    if reused:
        notes.append(f"★ words repeated within 30 days: {', '.join(reused)}")

    blocks = article_blocks(c)
    groups = set()
    for name, text in blocks.items():
        if STAR.search(text):
            groups.add(name.split("_")[0] if name.startswith(("world", "thailand")) else name)
    if len(groups) < 4:
        notes.append(f"★ words appear in only {len(groups)} article groups (target 4+)")

    ids = {s["id"] for s in sources}
    news = [("cover", c["cover"])] + [(f"world_{i}", a) for i, a in enumerate(c["world"], 1)] + \
           [(f"thailand_{i}", a) for i, a in enumerate(c["thailand"], 1)]
    for label, a in news:
        cited = set(a.get("source_ids", []))
        if not cited & ids:
            critical.append(f"{label} must list its source ids in source_ids")
        own = {s["id"] for s in sources if s["role"].split(" ")[0] == label}
        if own and not own & cited:
            notes.append(f"{label} does not cite its own source ({', '.join(sorted(own))})")
    for i in (1, 2):
        if sum(s["role"].startswith(f"thailand_{i}") for s in sources) == 1:
            notes.append(f"thailand_{i} has only one outlet as a source")

    _scripts(c, ("quick_route", "cover", "world", "thailand", "feature_a", "feature_b",
                 "two_takes", "say_it_better", "star_words", "recycled_words"), critical, notes)
    _length(sizes, ["cover", "world_1", "world_2", "thailand_1", "thailand_2",
                    "feature_a", "feature_b", "opinion"], critical, notes)
    return critical, notes


def check_lab(c: dict, plan: dict) -> tuple[list[str], list[str]]:
    critical, notes = [], []
    try:
        if len(c["activity"]["items"]) != 6 or len(c["activity"]["answers"]) != 6:
            critical.append("activity must have 6 items and 6 answers")
        if len(c["errors"]) != 5:
            critical.append("errors must have exactly 5 sentences")
        if len(c["word_workshop"]) != 3:
            critical.append("word_workshop must have 3 words")
        if len(c["other_language"]["phrases"]) != 3:
            critical.append("other_language must have 3 phrases")
        sizes = section_words(c)
    except (KeyError, TypeError, IndexError) as e:
        return [f"JSON structure is wrong or incomplete (missing {e})"], notes

    expected = len(c["star_words"]) + len(c["recycled_words"])
    if len(c["glossary"]) != expected:
        critical.append(f"glossary must have one entry for each of the {expected} words given")
    for g in c["glossary"]:
        if not THAI.search(g.get("thai", "")):
            critical.append(f"glossary entry '{g.get('word')}' needs a Thai translation in Thai script")

    lab = lab_text(c)
    missing = [w for w in c["star_words"] if not word_pattern(w).search(lab)]
    if missing:
        notes.append(f"★ words not practised in the Language Lab: {', '.join(missing)}")
    ws = {w["word"].lower() for w in c["word_workshop"]}
    if not ws <= {w.lower() for w in c["star_words"]}:
        notes.append("word_workshop uses a word that is not a ★ word")

    _scripts(c, ("word_workshop", "activity", "errors", "trap", "other_language", "glossary"), critical, notes)
    _length(sizes, ["language_lab", "other_language"], critical, notes)
    return critical, notes


def final_notes(c: dict) -> tuple[list[str], dict]:
    notes = []
    sizes = section_words(c)
    t = sizes["total"]
    if not (TOTAL[0] <= t <= TOTAL[1]):
        notes.append(f"whole issue: {t} words (target {TOTAL[0]}–{TOTAL[1]})")
    long_sentences = []
    for path, text in strings(c):
        for s in re.split(r"(?<=[.!?])\s+", plain(text)):
            if len(s.split()) > 35:
                long_sentences.append(f"{path} ({len(s.split())} words)")
    if long_sentences:
        notes.append("sentences over 35 words: " + "; ".join(long_sentences[:6]))
    return notes, sizes
