"""Build today's issue of THE CITY TIMES and rebuild the website.

    python scripts/build.py                  # today's issue (Bangkok time)
    python scripts/build.py --date 2026-11-02 --force
    python scripts/build.py --upgrade        # backup run (see below)
    PROVIDER=mock python scripts/build.py --date 2026-10-30 --news-fixture tests/fixtures/news.json

Flow
  1. plan      code: date, issue number, themes, words to avoid/review      (plan.py)
  2. news      code: RSS → pick 5 stories → download their text              (news.py)
  3. AI step 1 articles + Real English + trap + the day's words              (prompts/articles.md)
               → code auto-fix → checks → at most one repair → saved as a draft
  4. AI step 2 word bank + Language Lab + Other Languages                    (prompts/lab.md)
               → code auto-fix → checks → at most one repair
  5. publish   save data/issues/<date>.json, rebuild docs/ (index, archive, every issue)

Backup run (--upgrade): if today's issue was written by the fallback model,
write it again with the primary models; if they are still busy, keep it.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml

import autofix
import checks
import news
import render
from llm import Budget, LLMError, articles_schema, lab_schema, make_client
from plan import make_plan, today_in

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "issues"       # one JSON per published day: the paper's memory
DRAFTS = ROOT / "data" / "drafts"     # step-1 result, kept if step 2 fails
FAILED = ROOT / "data" / "failed"     # rejected AI answer, uploaded as a workflow artifact
DOCS = ROOT / "docs"                  # the website (GitHub Pages)
SUMMARY: list[str] = []               # shown on the workflow run page
SOURCE_FIELDS = ("id", "role", "source", "title", "url", "thin")


class StepFailed(RuntimeError):
    pass


def log(msg=""):
    print(msg, flush=True)


def write_summary():
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and SUMMARY:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(SUMMARY) + "\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def prompt(*names: str) -> str:
    return "\n\n".join((ROOT / "prompts" / f"{n}.md").read_text(encoding="utf-8") for n in names)


# ---------- what the AI is told ----------

def plan_for_articles(plan: dict) -> str:
    lines = [
        "# TODAY'S PLAN",
        f"Date: {plan['date_long']}",
        f"Feature theme: {plan['feature']}",
        f"Real English format: {plan['opinion']} (fill only this one)",
    ]
    if plan["opinion"] == "say_it_better":
        lines.append(f"Say It Better setting: {plan['setting']}")
    lines.append(f"Thai Speaker's Trap category: {plan['trap']}")
    lines.append("Avoid these as ★ words: " + (", ".join(plan["avoid_star_words"]) or "none"))
    if plan["review_pool"]:
        pool = ", ".join(r["word"] for r in plan["review_pool"])
        lines.append(f"↺ review candidates (use 4 that fit naturally): {pool}")
    else:
        lines.append("↺ review candidates: none yet. Choose 4 high-frequency B2 words.")
    for label, key in (("Real English", "recent_opinions"), ("Feature", "recent_features"),
                       ("Thai Speaker's Trap", "recent_traps")):
        if plan[key]:
            lines.append(f"Recent {label} topics — do not repeat: " + " | ".join(plan[key][:14]))
    return "\n".join(lines)


def sources_text(sources: list[dict]) -> str:
    out = ["# SOURCES"]
    for s in sources:
        short = " — SHORT" if s.get("thin") else ""
        out += [f"## {s['id']} — story: {s['role']} — {s['source']} — {s['title']}{short}", s["text"], ""]
    return "\n".join(out)


def plan_for_lab(plan: dict, a: dict) -> str:
    """Step 2 gets only the sentences with today's words + headlines, not whole articles."""
    lines = [
        "# TODAY'S PLAN",
        f"Language Lab activity: {plan['activity']}",
        f"Other Languages: {plan['language']} · everyday theme: {plan['phrase_theme']}",
    ]
    if plan["recent_language_notes"]:
        lines.append(f"Already used for {plan['language']} — do not repeat: " +
                     " | ".join(plan["recent_language_notes"][:40]))
    lines += ["", "# TODAY'S WORDS IN CONTEXT (word bank order)"]
    lines += [f'{kind} {w} — "{checks.plain(s)}"' for w, kind, s in autofix.words_in_context(a)]
    lines += ["", "# TODAY'S HEADLINES"]
    lines += [checks.plain(x["headline"]) for x in [a["cover"], *a["world"], *a["thailand"], a["feature"]]]
    return "\n".join(lines)


# ---------- one AI step: ask → auto-fix → check → (repair once) ----------

def run_step(name, client, system, user, schema, finish, repairs: int):
    """`finish(answer)` returns (result, critical, notes). Raises StepFailed if critical problems remain."""
    log(f"  asking the AI ({name})")
    answer = client.json(system, user, schema)
    for attempt in range(repairs + 1):
        result, critical, notes = finish(answer)
        log(f"  {name} check {attempt + 1}: {len(critical)} critical, {len(notes)} note(s)")
        for p in critical:
            log(f"    ✗ {p}")
        if not critical:
            return result, notes
        if attempt == repairs:
            break
        log(f"  asking the AI to repair {name}")
        previous = {k: result[k] for k in schema["properties"] if k in result}
        answer = client.json(system, user + "\n\n# YOUR PREVIOUS ANSWER\n" + json.dumps(previous, ensure_ascii=False) +
                             "\n\n# PROBLEMS TO FIX\n" + "\n".join(f"- {p}" for p in critical) +
                             "\n\nReturn the complete corrected JSON. Keep everything else the same.",
                             schema, temperature=0.4)
    raise StepFailed(f"{name}: still has critical problems after repair:\n" +
                     "\n".join(f"- {p}" for p in critical))


# ---------- website ----------

def prune(start: date, today: date):
    """Remove saved issues from before Issue 1, and drafts from earlier days."""
    for f in DATA.glob("*.json"):
        if f.stem < start.isoformat():
            f.unlink()
            log(f"  removed {f.name} (before Issue 1)")
    for f in DRAFTS.glob("*.json"):
        if f.stem < today.isoformat():
            f.unlink()


def rebuild_site(cfg: dict, e) -> None:
    """Render every saved issue, index.html (latest) and archive.html; delete pages with no saved issue."""
    issues = sorted((read_json(f) for f in DATA.glob("*.json")), key=lambda d: d["plan"]["date"])
    paper, keep = cfg["paper_name"], set()
    for i, issue in enumerate(issues):
        prev = issues[i - 1]["plan"]["date"] if i else None
        page = DOCS / "issues" / f"{issue['plan']['date']}.html"
        page.write_text(render.issue_page(e, paper, issue, prev, "../"), encoding="utf-8")
        keep.add(page.name)
    for page in (DOCS / "issues").glob("*.html"):
        if page.name not in keep:
            page.unlink()
    if not issues:
        for name in ("index.html", "archive.html"):
            (DOCS / name).unlink(missing_ok=True)
        return
    latest = issues[-1]
    prev = issues[-2]["plan"]["date"] if len(issues) > 1 else None
    (DOCS / "index.html").write_text(render.issue_page(e, paper, latest, prev, ""), encoding="utf-8")
    listing = [{"date": d["plan"]["date"], "date_long": d["plan"]["date_long"],
                "headline": checks.plain(d["content"]["cover"]["headline"])} for d in reversed(issues)]
    (DOCS / "archive.html").write_text(render.archive_page(e, paper, listing), encoding="utf-8")
    log(f"Site rebuilt: {len(issues)} issue(s), latest {latest['plan']['date']}")


# ---------- main ----------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in Bangkok)")
    ap.add_argument("--force", action="store_true", help="rebuild even if the issue exists (ignores drafts)")
    ap.add_argument("--upgrade", action="store_true",
                    help="backup run: rewrite today's issue with the primary models if it used the fallback model")
    ap.add_argument("--provider", help="gemini | anthropic | mock")
    ap.add_argument("--news-fixture", help="offline news JSON for testing")
    ap.add_argument("--site-only", action="store_true", help="only rebuild HTML from saved issues")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))
    for d in (DATA, DRAFTS, DOCS / "issues"):
        d.mkdir(parents=True, exist_ok=True)
    (DOCS / ".nojekyll").touch()
    e = render.env(ROOT / "templates")
    start = date.fromisoformat(str(cfg["start_date"]))
    day = date.fromisoformat(args.date) if args.date else today_in(cfg["timezone"])
    prune(start, day)

    if args.site_only or day < start:
        if day < start and not args.site_only:
            log(f"{day} is before Issue 1 ({start}): nothing to write.")
        rebuild_site(cfg, e)
        return 0

    # Is today's issue already done?
    data_file, draft_file = DATA / f"{day}.json", DRAFTS / f"{day}.json"
    upgrade = False
    if data_file.exists() and not args.force:
        if not read_json(data_file).get("fallback_used"):
            log(f"Issue for {day} already exists. Use --force to rebuild it.")
            return 0
        if not args.upgrade:
            log(f"Issue for {day} exists (fallback model). The backup run (--upgrade) will try the primary models.")
            return 0
        upgrade = True
        log(f"Issue for {day} was written by the fallback model: trying the primary models again.\n"
            "If they are still busy, the current issue stays online.")

    plan = make_plan(cfg, day, DATA)
    log(f"== {cfg['paper_name']} · {plan['date_long']} (Issue {plan['issue_no']})")
    log(f"   {plan['weekday']}: {plan['feature']} / {plan['opinion']} / {plan['activity']} / "
        f"{plan['trap']} / {plan['language']} ({plan['phrase_theme']})")
    SUMMARY.append(f"## {plan['date_long']} · Issue {plan['issue_no']}")

    provider = args.provider or os.environ.get("PROVIDER") or cfg.get("provider", "gemini")
    budget = Budget(int(cfg.get("max_requests_per_run", 12)))
    repairs = int(cfg.get("max_repairs_per_step", 1))
    notes: list[str] = []

    def fail(msg: str, rejected: dict | None = None) -> int:
        if upgrade:   # backup run: a failure only means "keep the issue we have"
            draft_file.unlink(missing_ok=True)
            log("Primary models not available: keeping the issue that is already online. " + msg.splitlines()[0])
            SUMMARY.append("ℹ️ Kept the existing issue (fallback model); the primary models were still unavailable.")
            write_summary()
            return 0
        log("ERROR: " + msg)
        if rejected:
            write_json(FAILED / f"{day}.json", rejected)
        SUMMARY.append("❌ Not published.\n\n" + msg)
        SUMMARY.append(f"\nAI requests used: {budget.used}/{budget.limit}\n" +
                       "\n".join(f"- {x}" for x in budget.log))
        write_summary()
        return 1

    try:
        client = make_client(provider, cfg, ROOT, budget, log)
    except LLMError as err:
        return fail(str(err))
    if upgrade and hasattr(client, "allow_fallback"):
        client.allow_fallback = False
    log(f"Provider: {provider} · request budget {budget.limit}")

    # ── news + AI step 1 (or reuse today's draft) ──
    if draft_file.exists() and not args.force and not upgrade:
        draft = read_json(draft_file)
        sources, a, draft_fallback = draft["sources"], draft["articles"], draft.get("fallback_used", False)
        notes += draft.get("notes", [])
        log("\nReusing today's finished articles from an earlier run (step 1 skipped).")
    else:
        log("\n[1/4] Collecting news")
        if args.news_fixture:
            sources = news.load_fixture(ROOT / args.news_fixture)["sources"]
        else:
            try:
                picks, sources = news.select_sources(news.collect(cfg, log), plan["recent_news_titles"], log)
            except (ValueError, KeyError, IndexError) as err:
                return fail(f"Not enough news ({err}). Check the feed lines in the log.")
            log(f"  picks: {picks}")

        log("\n[2/4] Writing the articles")

        def finish_articles(answer):
            n: list[str] = []
            art = autofix.fix_articles(answer, n)
            critical, more = checks.check_articles(art, plan, sources)
            return art, critical, n + more

        try:
            a, step_notes = run_step("articles", client, prompt("common", "articles"),
                                     plan_for_articles(plan) + "\n\n" + sources_text(sources),
                                     articles_schema(plan["opinion"]), finish_articles, repairs)
        except StepFailed as err:
            return fail(str(err), {"step": "articles"})
        except LLMError as err:
            return fail(str(err))
        notes += step_notes
        draft_fallback = bool(getattr(client, "used_fallback", False))
        write_json(draft_file, {"sources": [{k: s.get(k) for k in SOURCE_FIELDS} for s in sources],
                                "articles": a, "notes": step_notes, "fallback_used": draft_fallback})

    # ── AI step 2: word bank + lab + other languages ──
    log("\n[3/4] Writing the Word Bank, Language Lab and Other Languages")
    schema_b = lab_schema(len(a["star_words"]) + len(a["recycled_words"]))

    def finish_lab(answer):
        n: list[str] = []
        c = autofix.fix_lab({**a, **{k: answer[k] for k in answer if k in schema_b["properties"]}}, n)
        critical, more = checks.check_lab(c, plan)
        return c, critical, n + more

    retry_note = "\n\nThe articles are saved; the next run will only redo this step."
    try:
        content, step_notes = run_step("lab", client, prompt("common", "lab"), plan_for_lab(plan, a),
                                       schema_b, finish_lab, repairs)
    except StepFailed as err:
        return fail(str(err) + retry_note, {"step": "lab"})
    except LLMError as err:
        return fail(str(err) + retry_note)
    notes += step_notes

    # ── save + website ──
    log("\n[4/4] Publishing")
    more, sizes = checks.final_notes(content)
    notes += more
    for n in notes:
        log(f"    · {n}")
    fallback_used = bool(getattr(client, "used_fallback", False)) or draft_fallback
    write_json(data_file, {
        "plan": plan, "sources": [{k: s.get(k) for k in SOURCE_FIELDS} for s in sources],
        "content": content, "sizes": sizes, "notes": notes, "provider": provider,
        "requests": budget.log, "fallback_used": fallback_used,
    })
    draft_file.unlink(missing_ok=True)
    rebuild_site(cfg, e)

    fb = " · ⚠️ written with the fallback model; the backup run will try the primary models" if fallback_used else ""
    SUMMARY.append(f"✅ Published{' (rewritten with the primary model)' if upgrade else ''} · "
                   f"{sizes['total']} words · {budget.used} AI request(s){fb}\n\n"
                   f"Cover: {checks.plain(content['cover']['headline'])}")
    SUMMARY.append("\nRequests:\n" + "\n".join(f"- {x}" for x in budget.log))
    if notes:
        SUMMARY.append("\nNotes (published anyway):\n" + "\n".join(f"- {n}" for n in notes))
    write_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
