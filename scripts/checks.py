"""Automatic quality checks. Hard problems are sent back to the AI to fix;
soft problems are only reported in the run log."""
import re

THAI = re.compile(r"[฀-๿]")
CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")
STAR = re.compile(r"\*\*(.+?)\*\*")
MARKUP = re.compile(r"\*\*|__(?=\S)|(?<=\S)__")

RANGES = {  # body words, from the editorial plan
    "cover": (430, 470), "world": (140, 160), "thailand": (140, 160),
    "feature_a": (280, 320), "feature_b": (280, 320), "opinion": (260, 300),
    "language_lab": (380, 420), "other_language": (150, 180),
}
TOTAL = (2800, 3000)
HARD_TOTAL = (2500, 3300)


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
    """Reading articles that carry ★/↺ markup, keyed by a short name."""
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
    for w in c["word_workshop"]:
        parts += [w["word"], *w["collocations"], w["example"]]
    parts += [c["activity"]["instructions"], *c["activity"]["items"], *c["activity"]["answers"]]
    for e in c["errors"]:
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
    lab = {k: c[k] for k in ("word_workshop", "errors", "trap")}
    lab["activity"] = {k: c["activity"][k] for k in ("instructions", "items")}
    out["language_lab"] = sum(nwords(t) for _, t in strings(lab))
    out["other_language"] = sum(nwords(t) for _, t in strings(c["other_language"]))
    out["glossary"] = sum(nwords(t) for _, t in strings(c["glossary"]))
    out["total"] = sum(nwords(t) for _, t in strings(c))
    return out


def run(c: dict, plan: dict, sources: list[dict]) -> tuple[list[str], list[str], dict]:
    hard, soft = [], []

    def need(cond, msg, is_hard=True):
        if not cond:
            (hard if is_hard else soft).append(msg)

    # ---- structure
    try:
        need(len(c["world"]) == 2, "world must have exactly 2 articles")
        need(len(c["thailand"]) == 2, "thailand must have exactly 2 articles")
        need(len(c["quick_route"]) == 3, "quick_route must have 3 bullets")
        need(len(c["word_workshop"]) == 3, "word_workshop must have 3 words")
        need(len(c["activity"]["items"]) == 6, "activity must have 6 items")
        need(len(c["activity"]["answers"]) == 6, "activity must have 6 answers")
        need(len(c["errors"]) == 5, "errors must have 5 sentences")
        need(len(c["other_language"]["phrases"]) == 3, "other_language must have 3 phrases")
        need(len(c["glossary"]) == 12, "glossary must have 12 entries")
        need(bool(c.get(plan["opinion"])), f"the opinion slot today must be {plan['opinion']}")
        blocks = article_blocks(c)
        sizes = section_words(c)
    except (KeyError, TypeError, IndexError) as e:
        return [f"JSON structure is wrong or incomplete (missing {e})"], soft, {}

    # ---- vocabulary
    star = [w.strip() for w in c["star_words"]]
    rec = [w.strip() for w in c["recycled_words"]]
    need(len(star) == 8 and len({w.lower() for w in star}) == 8, "star_words must be 8 different words")
    need(len(rec) == 4 and len({w.lower() for w in rec}) == 4, "recycled_words must be 4 different words")
    need(not ({w.lower() for w in star} & {w.lower() for w in rec}), "star_words and recycled_words overlap")
    avoid = {w.lower() for w in plan.get("avoid_star_words", [])}
    reused = [w for w in star if w.lower() in avoid]
    need(not reused, f"these ★ words were used in the last 30 days, replace them: {', '.join(reused)}")

    marked = {name: [m.group(1) for m in STAR.finditer(text)] for name, text in blocks.items()}
    sections_with_star = set()
    lab = lab_text(c)
    for w in star:
        pat = word_pattern(w)
        where = {name for name, spans in marked.items() if any(pat.search(s) for s in spans)}
        need(bool(where), f"★ word '{w}' is not marked as **{w}** in any article")
        sections_with_star |= {n.split("_")[0] if n.startswith(("world", "thailand")) else n for n in where}
        need(bool(pat.search(lab)), f"★ word '{w}' does not appear in the Language Lab")
    need(len(sections_with_star) >= 4,
         f"★ words appear in only {len(sections_with_star)} article groups; spread them over at least 4", False)
    ws = {w["word"].lower() for w in c["word_workshop"]}
    need(ws <= {w.lower() for w in star}, "word_workshop words must be ★ words")
    all_text = plain("\n".join(blocks.values()))
    for w in rec:
        need(bool(word_pattern(w).search(all_text)), f"↺ word '{w}' does not appear in any article")

    gl = [g["word"].strip().lower() for g in c["glossary"]]
    need(gl[:8] == [w.lower() for w in star], "glossary entries 1–8 must be the ★ words in star_words order")
    need(gl[8:] == [w.lower() for w in rec], "glossary entries 9–12 must be the ↺ words in recycled_words order")
    norm = lambda s: re.sub(r"\W+", " ", plain(s)).strip().lower()  # noqa: E731
    article_norm = norm(all_text)
    for g in c["glossary"]:
        need(norm(g["example"]) in article_norm,
             f"glossary example for '{g['word']}' is not a sentence copied from the articles", False)
        need(bool(THAI.search(g["thai"])), f"glossary entry '{g['word']}' has no Thai translation")

    # ---- scripts and markup
    for path, text in strings(c):
        if THAI.search(text) and not (path.startswith("glossary") and path.endswith(".thai")) \
                and path != "trap.explanation":
            hard.append(f"Thai text is not allowed in {path}")
        if CJK.search(text) and not path.startswith("other_language"):
            hard.append(f"Chinese/Japanese characters are not allowed in {path}")
        if re.search(r"<[a-zA-Z/][^>]*>", text):
            soft.append(f"HTML tag found in {path} (it will be shown as text)")
        if re.search(r"\[[^\]]{1,40}\]", text) and not path.startswith(("activity", "errors")):
            soft.append(f"square-bracket text in {path} looks like a placeholder")
        if text.count("**") % 2:
            hard.append(f"unbalanced ** markup in {path}")

    # ---- sources
    ids = {s["id"] for s in sources}
    news = [("cover", c["cover"])] + [(f"world_{i}", a) for i, a in enumerate(c["world"], 1)] + \
           [(f"thailand_{i}", a) for i, a in enumerate(c["thailand"], 1)]
    for label, a in news:
        cited = set(a.get("source_ids", []))
        need(bool(cited), f"{label} has no source_ids")
        need(cited <= ids, f"{label} cites unknown sources: {', '.join(sorted(cited - ids))}")
        own = {s["id"] for s in sources if s["role"].split(" ")[0] == label}
        need(bool(own & cited) or not own, f"{label} does not cite its own source ({', '.join(sorted(own))})", False)
    for key in ("feature_a", "feature_b"):
        bad = set(c[key].get("source_ids", [])) - ids
        need(not bad, f"{key} cites unknown sources: {', '.join(sorted(bad))}")
    for i in (1, 2):
        roles = [s for s in sources if s["role"].startswith(f"thailand_{i}")]
        need(len(roles) >= 2 or not roles, f"thailand_{i} has only one outlet as a source", False)

    # ---- length
    for name, n in sizes.items():
        key = "world" if name.startswith("world") else "thailand" if name.startswith("thailand") else name
        if key not in RANGES:
            continue
        lo, hi = RANGES[key]
        if not (lo * 0.75 <= n <= hi * 1.25):
            hard.append(f"{name} has {n} words; it must be {lo}–{hi}")
        elif not (lo * 0.9 <= n <= hi * 1.1):
            soft.append(f"{name} has {n} words (target {lo}–{hi})")
    t = sizes["total"]
    if not (HARD_TOTAL[0] <= t <= HARD_TOTAL[1]):
        hard.append(f"the whole issue has {t} words; it must be {TOTAL[0]}–{TOTAL[1]}")
    elif not (TOTAL[0] <= t <= TOTAL[1]):
        soft.append(f"the whole issue has {t} words (target {TOTAL[0]}–{TOTAL[1]})")

    long_sentences = []
    for path, text in strings(c):
        for s in re.split(r"(?<=[.!?])\s+", plain(text)):
            if len(s.split()) > 35:
                long_sentences.append(f"{path}: \"{s[:60]}…\" ({len(s.split())} words)")
    if long_sentences:
        soft.append("sentences over 35 words: " + "; ".join(long_sentences[:6]))

    return hard, soft, sizes
