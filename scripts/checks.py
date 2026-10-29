"""Quality checks.

check_articles / check_lab return (critical, notes):
  critical  → sent back to the AI once to fix; if still there, the step fails
  notes     → only shown in the run summary; the issue is published anyway
Small problems are fixed by autofix.py before these checks run.
"""
import re

THAI = re.compile(r"[฀-๿]")
CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")
STAR = re.compile(r"\*\*(.+?)\*\*")                          # **word** = ★ word
MARKUP = re.compile(r"\*\*|__(?=[^_\s])|(?<=[^_\s])__")      # ** and __ (but not ______ gaps)

# body words per section, from the editorial plan
RANGES = {
    "cover": (470, 510), "world": (140, 160), "thailand": (140, 160),
    "feature": (310, 370), "real_english": (260, 320),
    "language_lab": (150, 260), "other_language": (140, 190),
}
TOTAL = (2300, 2700)
CRITICAL_LOW, CRITICAL_HIGH = 0.6, 1.5      # only far-off lengths are worth a repair
NEWS = ("cover", "world_1", "world_2", "thailand_1", "thailand_2")


# ---------- text helpers ----------

def plain(text: str) -> str:
    """Text without ** / __ markup."""
    return MARKUP.sub("", text or "")


def nwords(text: str) -> int:
    return len(plain(text).split())


def word_pattern(word: str) -> re.Pattern:
    """Loose match for a word or phrase in any form: "mitigate" matches "mitigated",
    "cut back on" matches "cutting back on". No leading \\b, so **word** and __word__ match."""
    parts = []
    for tok in re.findall(r"[A-Za-z]+", word.lower()):
        if len(tok) >= 4 and tok[-1] in "ey":
            tok = tok[:-1]
        parts.append(re.escape(tok) + (r"\w*" if len(tok) >= 3 else ""))
    return re.compile(r"(?<![A-Za-z])" + r"[\s-]+".join(parts) if parts else r"$^", re.I)


def strings(obj, path=""):
    """Yield (path, text) for every string in nested content (source ids excluded)."""
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
    """Reading text of each article (where ★/↺ words are marked), keyed by name."""
    def art(a):
        return a["headline"] + "\n" + "\n".join(a["paragraphs"])
    blocks = {"cover": art(c["cover"])}
    for key in ("world", "thailand"):
        for i, a in enumerate(c[key], 1):
            blocks[f"{key}_{i}"] = art(a)
    blocks["feature"] = art(c["feature"])
    if c.get("two_takes"):
        t = c["two_takes"]
        blocks["real_english"] = "\n".join([t["question"], t["view_a"]["text"], t["view_b"]["text"]])
    elif c.get("say_it_better"):
        s = c["say_it_better"]
        blocks["real_english"] = "\n".join([s["situation"], s["formal"], s["neutral"], s["casual"]])
    return blocks


def lab_text(c: dict) -> str:
    act = c.get("activity", {})
    parts = [act.get("instructions", ""), *act.get("items", []), *act.get("answers", [])]
    for e in c.get("errors", []):
        parts += [e["sentence"], e["corrected"]]
    return plain("\n".join(parts))


def section_words(c: dict) -> dict[str, int]:
    out = {"cover": sum(nwords(p) for p in c["cover"]["paragraphs"])}
    for key in ("world", "thailand"):
        for i, a in enumerate(c[key], 1):
            out[f"{key}_{i}"] = sum(nwords(p) for p in a["paragraphs"])
    out["feature"] = sum(nwords(p) for p in c["feature"]["paragraphs"])
    real = {"slot": c.get("two_takes") or c.get("say_it_better") or {}, "trap": c.get("trap") or {}}
    out["real_english"] = sum(nwords(t) for _, t in strings(real))
    if "activity" in c:   # step 2 is done
        lab = [c["activity"]["instructions"], *c["activity"]["items"], *(e["sentence"] for e in c["errors"])]
        out["language_lab"] = sum(nwords(t) for t in lab)
        out["other_language"] = sum(nwords(t) for _, t in strings(c["other_language"]))
        out["word_bank"] = sum(nwords(t) for _, t in strings(c.get("word_bank", [])))
    out["total"] = sum(nwords(t) for _, t in strings(c))
    return out


def _length(sizes: dict, names, critical: list, notes: list):
    for name in names:
        if name not in sizes:
            continue
        lo, hi = RANGES[name.split("_")[0] if name.startswith(("world", "thailand")) else name]
        n = sizes[name]
        if not lo * CRITICAL_LOW <= n <= hi * CRITICAL_HIGH:
            critical.append(f"{name} has {n} words; it must be {lo}–{hi}")
        elif not lo * 0.9 <= n <= hi * 1.1:
            notes.append(f"{name}: {n} words (target {lo}–{hi})")


def _scripts(part: dict, critical: list, thai_ok=lambda path: False):
    """Thai only where allowed; no Chinese/Japanese anywhere."""
    for path, text in strings(part):
        if THAI.search(text) and not thai_ok(path):
            critical.append(f"Thai text is not allowed in {path}; write it in English")
        if CJK.search(text):
            critical.append(f"Chinese/Japanese characters are not allowed in {path}")


# ---------- step 1 ----------

def check_articles(c: dict, plan: dict, sources: list[dict]) -> tuple[list[str], list[str]]:
    critical, notes = [], []
    try:
        if len(c["world"]) != 2 or len(c["thailand"]) != 2:
            critical.append("world and thailand must have exactly 2 articles each")
        if not c.get(plan["opinion"]):
            critical.append(f"the Real English format today must be {plan['opinion']}")
        trap = c["trap"]
        if not trap.get("wrong") or not trap.get("right"):
            critical.append("trap needs a wrong sentence and a right sentence")
        sizes = section_words(c)
        blocks = article_blocks(c)
    except (KeyError, TypeError, IndexError) as e:
        return [f"JSON structure is wrong or incomplete (missing {e})"], notes

    # vocabulary (autofix has already dropped words that are not in the text)
    if len(c["star_words"]) < 6:
        critical.append("fewer than 6 ★ words could be found in the articles; use 8 ★ words in the article text")
    avoid = {w.lower() for w in plan.get("avoid_star_words", [])}
    reused = [w for w in c["star_words"] if w.lower() in avoid]
    if reused:
        notes.append(f"★ words repeated within 30 days: {', '.join(reused)}")
    groups = {n.split("_")[0] for n, text in blocks.items() if STAR.search(text)}
    if len(groups) < 4:
        notes.append(f"★ words appear in only {len(groups)} parts of the issue (target 4+)")

    # sources
    ids = {s["id"] for s in sources}
    news = [("cover", c["cover"])] + [(f"world_{i}", a) for i, a in enumerate(c["world"], 1)] + \
           [(f"thailand_{i}", a) for i, a in enumerate(c["thailand"], 1)]
    for label, a in news:
        cited = set(a.get("source_ids", []))
        own = {s["id"] for s in sources if s["role"].split(" ")[0] == label}
        if not cited & ids:
            critical.append(f"{label} must list its source ids in source_ids")
        elif own and not own & cited:
            notes.append(f"{label} does not cite its own source ({', '.join(sorted(own))})")
    for label in ("thailand_1", "thailand_2"):
        if sum(s["role"].startswith(label) for s in sources) == 1:
            notes.append(f"{label} has only one outlet as a source")

    # scripts and lengths
    keys = ("cover", "world", "thailand", "feature", "two_takes", "say_it_better", "trap",
            "star_words", "recycled_words")
    _scripts({k: c[k] for k in keys if k in c}, critical, thai_ok=lambda p: p == "trap.explanation")
    thin = {s["role"].split(" ")[0] for s in sources if s.get("thin")}
    _length(sizes, [n for n in NEWS if n not in thin] + ["feature", "real_english"], critical, notes)
    for n in sorted(thin):   # never push the AI to pad a story it has few facts for
        notes.append(f"{n}: little source text available ({sizes.get(n)} words written)")
    return critical, notes


# ---------- step 2 ----------

def check_lab(c: dict, plan: dict) -> tuple[list[str], list[str]]:
    critical, notes = [], []
    try:
        if len(c["activity"]["items"]) != 6 or len(c["activity"]["answers"]) != 6:
            critical.append("activity must have 6 items and 6 answers")
        if len(c["errors"]) != 5:
            critical.append("errors must have exactly 5 sentences")
        ol = c["other_language"]
        if len(ol["phrases"]) != 3:
            critical.append("other_language must have 3 phrases")
        if not (ol["root"]["root"] and ol["borrowed"]["word"] and ol["curious"]["word"]):
            critical.append("other_language needs root, borrowed and curious words")
        sizes = section_words(c)
    except (KeyError, TypeError, IndexError) as e:
        return [f"JSON structure is wrong or incomplete (missing {e})"], notes

    star = {w.lower() for w in c["star_words"]}
    expected = len(c["star_words"]) + len(c["recycled_words"])
    if len(c["word_bank"]) != expected:
        critical.append(f"word_bank must have one entry for each of the {expected} words given")
    for g in c["word_bank"]:
        if not THAI.search(g.get("thai", "")):
            critical.append(f"word_bank entry '{g.get('word')}' needs a Thai translation in Thai script")
        if g["word"].lower() in star and len(g.get("collocations", [])) < 2:
            notes.append(f"word_bank: few collocations for '{g['word']}'")
        if not g.get("example"):
            notes.append(f"word_bank: no example sentence found for '{g['word']}'")

    lab = lab_text(c)
    practised = sum(bool(word_pattern(w).search(lab)) for w in c["star_words"])
    if practised < 5:
        notes.append(f"only {practised} ★ words are practised in the Language Lab")

    _scripts({k: c[k] for k in ("word_bank", "activity", "errors", "other_language")}, critical,
             thai_ok=lambda p: p.endswith(".thai"))
    _length(sizes, ["language_lab", "other_language"], critical, notes)
    return critical, notes


# ---------- whole issue ----------

def final_notes(c: dict) -> tuple[list[str], dict]:
    notes, sizes = [], section_words(c)
    if not TOTAL[0] <= sizes["total"] <= TOTAL[1]:
        notes.append(f"whole issue: {sizes['total']} words (target {TOTAL[0]}–{TOTAL[1]})")
    long_sentences = [f"{path} ({len(s.split())} words)" for path, text in strings(c)
                      for s in re.split(r"(?<=[.!?])\s+", plain(text)) if len(s.split()) > 35]
    if long_sentences:
        notes.append("sentences over 35 words: " + "; ".join(long_sentences[:6]))
    return notes, sizes
