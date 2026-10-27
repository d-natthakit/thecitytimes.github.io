"""Small fixes done by code, so the AI is not asked to rewrite a whole step
for a missing ** or a glossary in the wrong order."""
import re

from checks import word_pattern

TAG = re.compile(r"<[^>]{1,80}>")


def _clean_all(obj):
    """Remove HTML tags and broken ** markup in place."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = _clean(v)
            else:
                _clean_all(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = _clean(v)
            else:
                _clean_all(v)


def _clean(t: str) -> str:
    t = TAG.sub("", t)
    if t.count("**") % 2:
        t = t.replace("**", "")
    return t.strip()


def _unique(words) -> list[str]:
    out, seen = [], set()
    for w in words or []:
        w = re.sub(r"[*_]", "", str(w)).strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def _text_slots(a: dict):
    """(container, key) for every article text field, in reading order."""
    slots = []
    for key in ("cover",):
        slots += [(a[key]["paragraphs"], i) for i in range(len(a[key]["paragraphs"]))]
    for key in ("world", "thailand"):
        for art in a.get(key, []):
            slots += [(art["paragraphs"], i) for i in range(len(art["paragraphs"]))]
    for key in ("feature_a", "feature_b"):
        slots += [(a[key]["paragraphs"], i) for i in range(len(a[key]["paragraphs"]))]
    if a.get("two_takes"):
        slots += [(a["two_takes"]["view_a"], "text"), (a["two_takes"]["view_b"], "text")]
    if a.get("say_it_better"):
        s = a["say_it_better"]
        slots += [(s, k) for k in ("situation", "formal", "neutral", "casual")]
    return slots


def _inside_markup(text: str, pos: int) -> bool:
    before = text[:pos]
    return before.count("**") % 2 == 1 or len(re.findall(r"(?<![_\w])__(?=\S)", before)) > \
        len(re.findall(r"(?<=\S)__(?![_\w])", before))


def _marked(slots, mark: str, pat) -> bool:
    q = re.escape(mark)
    span = re.compile(q + r"(.+?)" + q)
    return any(pat.search(m.group(1)) for c, k in slots for m in span.finditer(c[k]))


def _mark_first(slots, mark: str, pat) -> bool:
    for c, k in slots:
        text = c[k]
        for m in pat.finditer(text):
            if not _inside_markup(text, m.start()):
                c[k] = text[:m.start()] + mark + m.group(0) + mark + text[m.end():]
                return True
    return False


def fix_articles(a: dict, notes: list) -> dict:
    _clean_all(a)
    a["star_words"] = _unique(a.get("star_words"))[:8]
    a["recycled_words"] = [w for w in _unique(a.get("recycled_words"))
                           if w.lower() not in {s.lower() for s in a["star_words"]}][:4]
    try:
        slots = _text_slots(a)
    except (KeyError, TypeError):
        return a                       # structure problem: the checks will report it
    for key, mark in (("star_words", "**"), ("recycled_words", "__")):
        kept = []
        for w in a[key]:
            pat = word_pattern(w)
            if _marked(slots, mark, pat) or _mark_first(slots, mark, pat):
                kept.append(w)
            else:
                notes.append(f"dropped '{w}' from {key}: it does not appear in the articles")
        a[key] = kept
    return a


def _sentences(slots):
    for c, k in slots:
        for s in re.split(r"(?<=[.!?])\s+", c[k]):
            yield s.strip()


def fix_lab(c: dict, notes: list) -> dict:
    _clean_all({k: c[k] for k in ("word_workshop", "activity", "errors", "trap", "other_language", "glossary")
                if k in c})
    wanted = c["star_words"] + c["recycled_words"]

    # glossary: match entries to the expected words and put them in order
    entries = list(c.get("glossary", []))
    ordered = []
    for w in wanted:
        pat = word_pattern(w)
        hit = next((g for g in entries if g.get("word", "").strip().lower() == w.lower()), None) or \
            next((g for g in entries if pat.search(g.get("word", ""))), None)
        if hit:
            entries.remove(hit)
            ordered.append({**hit, "word": w})
        else:
            notes.append(f"glossary had no entry for '{w}'")
    c["glossary"] = ordered

    # glossary examples: the first sentence in the articles that uses the word
    try:
        sentences = list(_sentences(_text_slots(c)))
    except (KeyError, TypeError):
        sentences = []
    for g in c["glossary"]:
        pat = word_pattern(g["word"])
        g["example"] = next((s for s in sentences if pat.search(s)), "")

    # word workshop words spelled exactly like the ★ words
    for item in c.get("word_workshop", []):
        for w in c["star_words"]:
            if item.get("word", "").lower() != w.lower() and word_pattern(w).search(item.get("word", "")):
                item["word"] = w
    return c
