"""Small fixes done by code, so the AI is never asked to rewrite a whole step
for a forgotten ** or a word bank in the wrong order."""
import re

from checks import word_pattern

TAG = re.compile(r"<[^>]{1,80}>")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


# ---------- helpers ----------

def _clean(t: str) -> str:
    """Remove HTML tags; drop ** markup if it is unbalanced."""
    t = TAG.sub("", t)
    if t.count("**") % 2:
        t = t.replace("**", "")
    return t.strip()


def _clean_all(obj):
    """Apply _clean to every string in nested dicts/lists, in place."""
    items = obj.items() if isinstance(obj, dict) else enumerate(obj) if isinstance(obj, list) else []
    for k, v in list(items):
        if isinstance(v, str):
            obj[k] = _clean(v)
        else:
            _clean_all(v)


def _unique(words) -> list[str]:
    """Strip markup and duplicates from a word list, keeping order."""
    out, seen = [], set()
    for w in words or []:
        w = re.sub(r"[*_]", "", str(w)).strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def _text_slots(a: dict) -> list[tuple]:
    """(container, key) of every article text field, in reading order.
    Using (container, key) lets us edit the text in place."""
    slots = [(a["cover"]["paragraphs"], i) for i in range(len(a["cover"]["paragraphs"]))]
    for key in ("world", "thailand"):
        for art in a.get(key, []):
            slots += [(art["paragraphs"], i) for i in range(len(art["paragraphs"]))]
    slots += [(a["feature"]["paragraphs"], i) for i in range(len(a["feature"]["paragraphs"]))]
    if a.get("two_takes"):
        slots += [(a["two_takes"]["view_a"], "text"), (a["two_takes"]["view_b"], "text")]
    if a.get("say_it_better"):
        slots += [(a["say_it_better"], k) for k in ("situation", "formal", "neutral", "casual")]
    return slots


def _sentences(slots):
    for container, key in slots:
        for s in SENTENCE_END.split(container[key]):
            yield s.strip()


def _inside_markup(text: str, pos: int) -> bool:
    """Is position `pos` already inside **...** or __...__?"""
    before = text[:pos]
    opens = len(re.findall(r"(?<![_\w])__(?=\S)", before))
    closes = len(re.findall(r"(?<=\S)__(?![_\w])", before))
    return before.count("**") % 2 == 1 or opens > closes


def _is_marked(slots, mark: str, pat) -> bool:
    span = re.compile(re.escape(mark) + r"(.+?)" + re.escape(mark))
    return any(pat.search(m.group(1)) for c, k in slots for m in span.finditer(c[k]))


def _mark_first(slots, mark: str, pat) -> bool:
    """Wrap the first unmarked occurrence of the word in `mark`. False if not found."""
    for container, key in slots:
        text = container[key]
        for m in pat.finditer(text):
            if not _inside_markup(text, m.start()):
                container[key] = f"{text[:m.start()]}{mark}{m.group(0)}{mark}{text[m.end():]}"
                return True
    return False


def _first_sentence_with(sentences: list[str], word: str) -> str:
    pat = word_pattern(word)
    return next((s for s in sentences if pat.search(s)), "")


# ---------- step 1 ----------

def fix_articles(a: dict, notes: list) -> dict:
    """Clean text, then make sure every ★/↺ word is marked in the articles.
    Words that do not appear at all are dropped (with a note)."""
    _clean_all(a)
    a["star_words"] = _unique(a.get("star_words"))[:8]
    star = {w.lower() for w in a["star_words"]}
    a["recycled_words"] = [w for w in _unique(a.get("recycled_words")) if w.lower() not in star][:4]
    try:
        slots = _text_slots(a)
    except (KeyError, TypeError):
        return a                      # broken structure: the checks will report it
    for key, mark in (("star_words", "**"), ("recycled_words", "__")):
        kept = []
        for w in a[key]:
            pat = word_pattern(w)
            if _is_marked(slots, mark, pat) or _mark_first(slots, mark, pat):
                kept.append(w)
            else:
                notes.append(f"dropped '{w}' from {key}: it does not appear in the articles")
        a[key] = kept
    return a


def words_in_context(a: dict) -> list[tuple[str, str, str]]:
    """(word, ★|↺, sentence) for the step-2 prompt: far shorter than sending whole articles."""
    sentences = list(_sentences(_text_slots(a)))
    return [(w, kind, _first_sentence_with(sentences, w))
            for kind, key in (("★", "star_words"), ("↺", "recycled_words")) for w in a[key]]


# ---------- step 2 ----------

def fix_lab(c: dict, notes: list) -> dict:
    """Put the word bank in ★-then-↺ order, fix spelling of the words,
    keep collocations for ★ words only, and add an example sentence from the articles."""
    _clean_all({k: c[k] for k in ("word_bank", "activity", "errors", "other_language") if k in c})
    star = {w.lower() for w in c["star_words"]}
    entries = list(c.get("word_bank", []))
    ordered = []
    for w in c["star_words"] + c["recycled_words"]:
        pat = word_pattern(w)
        hit = next((g for g in entries if g.get("word", "").strip().lower() == w.lower()), None) or \
            next((g for g in entries if pat.search(g.get("word", ""))), None)
        if not hit:
            notes.append(f"word bank had no entry for '{w}'")
            continue
        entries.remove(hit)
        colls = [x for x in hit.get("collocations", []) if x.strip()][:3] if w.lower() in star else []
        ordered.append({**hit, "word": w, "collocations": colls})

    sentences = list(_sentences(_text_slots(c)))
    for g in ordered:
        g["example"] = _first_sentence_with(sentences, g["word"])
    c["word_bank"] = ordered
    return c
