"""Turn the checked content JSON into static HTML pages."""
import math
import re
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from checks import plain as strip_markup

LANG_CODES = {"German": "de", "Spanish": "es", "Chinese": "zh-Hans", "French": "fr", "Italian": "it"}
THAI_RUN = re.compile(r"([฀-๿][฀-๿\s]*[฀-๿]|[฀-๿])")


def md(text: str) -> Markup:
    s = str(escape(text or ""))
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__([^_\s][^_]*?)__", r'<span class="rw">\1</span>', s)
    return Markup(s)


def thai(text: str) -> Markup:
    s = str(escape(strip_markup(text or "")))
    return Markup(THAI_RUN.sub(r'<span lang="th">\1</span>', s))


def short_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b')}"


def env(template_dir: Path) -> Environment:
    e = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape(["j2", "html"]),
                    trim_blocks=True, lstrip_blocks=True)
    e.filters["md"] = md
    e.filters["thai"] = thai
    e.filters["plain"] = strip_markup
    e.filters["paras"] = lambda t: [p.strip() for p in re.split(r"\n\s*\n|\n", t or "") if p.strip()]
    e.filters["minutes"] = lambda n: max(1, math.ceil((n or 0) / 150))
    return e


def issue_page(e: Environment, paper: str, plan: dict, content: dict, sizes: dict,
               sources: list[dict], prev: str | None, root: str, fmt: int = 2) -> str:
    cited = {sid for k in ("cover", "feature", "feature_a", "feature_b") if k in content
             for sid in content[k].get("source_ids", [])}
    cited |= {sid for k in ("world", "thailand") for a in content[k] for sid in a.get("source_ids", [])}
    used = [s for s in sources if s["id"] in cited] or sources
    review_from = {r["word"].lower(): short_date(r["taught_date"])
                   for r in plan.get("review_pool", []) if r.get("taught_date")}
    template = "issue.html.j2" if fmt >= 2 else "issue_v1.html.j2"
    return e.get_template(template).render(
        paper=paper, plan=plan, c=content, sizes=sizes, sources=used, prev=prev, root=root,
        lang_code=LANG_CODES.get(plan["language"], "und"), review_from=review_from)


def archive_page(e: Environment, paper: str, issues: list[dict]) -> str:
    return e.get_template("archive.html.j2").render(paper=paper, issues=issues, root="")
