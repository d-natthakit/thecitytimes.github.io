"""Turn saved issues into static HTML pages (Jinja2 templates in templates/)."""
import math
import re
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from checks import plain as strip_markup

LANG_CODES = {"German": "de", "Spanish": "es", "French": "fr", "Italian": "it"}
THAI_RUN = re.compile(r"([฀-๿][฀-๿\s]*[฀-๿]|[฀-๿])")
READING_SPEED = 150   # words per minute, for "B2 · 3 min" labels


# ---------- template filters ----------

def md(text: str) -> Markup:
    """**word** → <strong> (★ word), __word__ → <span class="rw"> (↺ word). Everything else is escaped."""
    s = str(escape(text or ""))
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__([^_\s][^_]*?)__", r'<span class="rw">\1</span>', s)
    return Markup(s)


def thai(text: str) -> Markup:
    """Escape text and tag Thai runs with lang="th" (for the Thai font)."""
    s = str(escape(strip_markup(text or "")))
    return Markup(THAI_RUN.sub(r'<span lang="th">\1</span>', s))


def short_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b')}"


def env(template_dir: Path) -> Environment:
    e = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape(["j2", "html"]),
                    trim_blocks=True, lstrip_blocks=True)
    e.filters.update({
        "md": md,
        "thai": thai,
        "plain": strip_markup,
        "paras": lambda t: [p.strip() for p in re.split(r"\n+", t or "") if p.strip()],
        "minutes": lambda n: max(1, math.ceil((n or 0) / READING_SPEED)),
    })
    return e


# ---------- pages ----------

def issue_page(e: Environment, paper: str, issue: dict, prev: str | None, root: str) -> str:
    """One day's page. `root` is "" for index.html and "../" for issues/<date>.html."""
    content, plan = issue["content"], issue["plan"]
    cited = {sid for k in ("cover", "feature") for sid in content[k].get("source_ids", [])}
    cited |= {sid for k in ("world", "thailand") for a in content[k] for sid in a.get("source_ids", [])}
    sources = [s for s in issue["sources"] if s["id"] in cited] or issue["sources"]
    review_from = {r["word"].lower(): short_date(r["taught_date"]) for r in plan.get("review_pool", [])}
    return e.get_template("issue.html.j2").render(
        paper=paper, plan=plan, c=content, sizes=issue["sizes"], sources=sources, prev=prev, root=root,
        lang_code=LANG_CODES.get(plan["language"], "und"), review_from=review_from)


def archive_page(e: Environment, paper: str, issues: list[dict]) -> str:
    return e.get_template("archive.html.j2").render(paper=paper, issues=issues, root="")
