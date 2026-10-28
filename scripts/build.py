"""Build today's issue of THE BKK GEM POST.

    python scripts/build.py                 # today's issue (Bangkok time)
    python scripts/build.py --date 2026-10-28 --force
    PROVIDER=mock python scripts/build.py --news-fixture tests/fixtures/news.json

Steps: plan (code) → news (RSS + code picks stories) → AI step 1: articles →
code auto-fix + checks (one repair if critical) → AI step 2: word bank + lab + other languages →
code auto-fix + checks (one repair if critical) → HTML.
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
DATA = ROOT / "data" / "issues"
DRAFTS = ROOT / "data" / "drafts"
DOCS = ROOT / "docs"
SUMMARY = []
FORMAT = 2          # content layout version (1 = issues before 29 Oct 2026)


class StepFailed(RuntimeError):
    pass


def log(msg=""):
    print(msg, flush=True)


def write_summary():
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and SUMMARY:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(SUMMARY) + "\n")


def prompt(*names: str) -> str:
    return "\n\n".join((ROOT / "prompts" / f"{n}.md").read_text(encoding="utf-8") for n in names)


# ---------- prompt inputs ----------

def plan_articles(plan: dict) -> str:
    lines = [
        "# TODAY'S PLAN",
        f"Date: {plan['date_long']} (Issue {plan['issue_no']})",
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


def plan_lab(plan: dict, a: dict) -> str:
    lines = [
        "# TODAY'S PLAN",
        f"Language Lab activity: {plan['activity']}",
        f"Other Languages: {plan['language']} · everyday theme: {plan['phrase_theme']}",
    ]
    if plan["recent_language_notes"]:
        lines.append(f"Already used for {plan['language']} — do not repeat: " +
                     " | ".join(plan["recent_language_notes"][:40]))
    lines += ["", "# TODAY'S WORDS IN CONTEXT (word bank order)"]
    for w, kind, sentence in autofix.words_in_context(a):
        lines.append(f"{kind} {w} — \"{checks.plain(sentence)}\"")
    lines += ["", "# TODAY'S HEADLINES"]
    lines += [checks.plain(x["headline"]) for x in [a["cover"], *a["world"], *a["thailand"], a["feature"]]]
    return "\n".join(lines)


def sources_text(sources: list[dict]) -> str:
    out = ["# SOURCES"]
    for s in sources:
        short = " — SHORT" if s.get("thin") else ""
        out += [f"## {s['id']} — story: {s['role']} — {s['source']} — {s['title']}{short}", s["text"], ""]
    return "\n".join(out)


# ---------- one AI step with auto-fix, checks and at most one repair ----------

def run_step(name, client, system, user, schema, finish, repairs: int):
    log(f"  asking the AI ({name})")
    out = client.json(system, user, schema)
    for attempt in range(repairs + 1):
        result, critical, notes = finish(out)
        log(f"  {name} check {attempt + 1}: {len(critical)} critical, {len(notes)} note(s)")
        for p in critical:
            log(f"    ✗ {p}")
        if not critical:
            return result, notes
        if attempt == repairs:
            raise StepFailed(f"{name}: still has critical problems after repair:\n" +
                             "\n".join(f"- {p}" for p in critical))
        log(f"  asking the AI to repair {name}")
        part = {k: result[k] for k in schema["properties"] if k in result}
        out = client.json(system, user + "\n\n# YOUR PREVIOUS ANSWER\n" + json.dumps(part, ensure_ascii=False) +
                          "\n\n# PROBLEMS TO FIX\n" + "\n".join(f"- {p}" for p in critical) +
                          "\n\nReturn the complete corrected JSON. Keep everything else the same.",
                          schema, temperature=0.4)
    raise StepFailed(name)


# ---------- site ----------

def rebuild_site(cfg: dict, e) -> None:
    issues = []
    for f in sorted(DATA.glob("*.json")):
        try:
            issues.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            log(f"  ! skipped unreadable {f.name}")
    if not issues:
        return
    issues.sort(key=lambda d: d["plan"]["date"])
    paper = cfg["paper_name"]
    for i, d in enumerate(issues):
        d["_prev"] = issues[i - 1]["plan"]["date"] if i else None
        out = DOCS / "issues" / f"{d['plan']['date']}.html"
        out.write_text(render.issue_page(e, paper, d["plan"], d["content"], d["sizes"], d["sources"],
                                         d["_prev"], "../", d.get("format", 1)), encoding="utf-8")
    latest = issues[-1]
    (DOCS / "index.html").write_text(render.issue_page(
        e, paper, latest["plan"], latest["content"], latest["sizes"], latest["sources"], latest["_prev"], "",
        latest.get("format", 1)),
        encoding="utf-8")
    listing = [{"issue_no": d["plan"]["issue_no"], "date": d["plan"]["date"], "date_long": d["plan"]["date_long"],
                "headline": checks.plain(d["content"]["cover"]["headline"])} for d in reversed(issues)]
    (DOCS / "archive.html").write_text(render.archive_page(e, paper, listing), encoding="utf-8")
    log(f"Site rebuilt: {len(issues)} issue(s), latest {latest['plan']['date']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in Bangkok)")
    ap.add_argument("--force", action="store_true", help="rebuild even if the issue exists (ignores drafts)")
    ap.add_argument("--provider", help="gemini | anthropic | mock")
    ap.add_argument("--news-fixture", help="offline news JSON for testing")
    ap.add_argument("--site-only", action="store_true", help="only rebuild HTML from saved issues")
    ap.add_argument("--upgrade", action="store_true",
                    help="backup run: if today's issue was written by the fallback model, "
                         "try again with the primary models (keeps the issue if they are busy)")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))
    for d in (DATA, DRAFTS, DOCS / "issues"):
        d.mkdir(parents=True, exist_ok=True)
    (DOCS / ".nojekyll").touch()
    e = render.env(ROOT / "templates")
    if args.site_only:
        rebuild_site(cfg, e)
        return 0

    day = date.fromisoformat(args.date) if args.date else today_in(cfg["timezone"])
    for old in DRAFTS.glob("*.json"):          # drafts from earlier days are no longer useful
        if old.stem < day.isoformat():
            old.unlink()
    data_file = DATA / f"{day.isoformat()}.json"
    draft_file = DRAFTS / f"{day.isoformat()}.json"
    upgrade = False
    if data_file.exists() and not args.force:
        existing = json.loads(data_file.read_text(encoding="utf-8"))
        if not existing.get("fallback_used"):
            log(f"Issue for {day} already exists. Use --force to rebuild it.")
            return 0
        if not args.upgrade:
            log(f"Issue for {day} exists (written by the fallback model). "
                "The backup run (--upgrade) will try the primary models.")
            return 0
        upgrade = True
        log(f"Issue for {day} was written by the fallback model: trying again with the primary models.\n"
            "If they are still busy, the current issue stays online.")

    plan = make_plan(cfg, day, DATA)
    log(f"== {cfg['paper_name']} · Issue {plan['issue_no']} · {plan['date_long']}")
    log(f"   {plan['weekday']}: {plan['feature']} / {plan['opinion']} / "
        f"{plan['activity']} / {plan['trap']} / {plan['language']}")
    SUMMARY.append(f"## Issue {plan['issue_no']} · {plan['date_long']}")

    provider = args.provider or os.environ.get("PROVIDER") or cfg.get("provider", "gemini")
    budget = Budget(int(cfg.get("max_requests_per_run", 8)))
    repairs = int(cfg.get("max_repairs_per_step", 1))
    notes: list[str] = []

    def finish_fail(msg: str, part: dict | None = None) -> int:
        if upgrade:
            draft_file.unlink(missing_ok=True)
            log("Primary models not available: keeping the issue that is already online. " + msg.splitlines()[0])
            SUMMARY.append("ℹ️ Kept the existing issue (written by the fallback model); "
                           "primary models were still unavailable.")
            write_summary()
            return 0
        log("ERROR: " + msg)
        if part:
            fail = ROOT / "data" / "failed"
            fail.mkdir(exist_ok=True)
            (fail / f"{day.isoformat()}.json").write_text(json.dumps(part, ensure_ascii=False, indent=1), "utf-8")
        SUMMARY.append("❌ Not published.\n\n" + msg)
        SUMMARY.append(f"\nAI requests used: {budget.used}/{budget.limit}\n" +
                       "\n".join(f"- {x}" for x in budget.log))
        write_summary()
        return 1

    try:
        client = make_client(provider, cfg, ROOT, budget, log)
    except LLMError as err:
        return finish_fail(str(err))
    log(f"Provider: {provider} · request budget {budget.limit}")
    if upgrade and hasattr(client, "allow_fallback"):
        client.allow_fallback = False

    draft, draft_fallback = None, False
    if draft_file.exists() and not args.force and not upgrade and \
            json.loads(draft_file.read_text(encoding="utf-8")).get("format") == FORMAT:
        draft = json.loads(draft_file.read_text(encoding="utf-8"))
        log("\nFound today's finished articles from an earlier run: reusing them (step 1 skipped).")
        sources, a = draft["sources"], draft["articles"]
        notes += draft.get("notes", [])
        draft_fallback = draft.get("fallback_used", False)
    else:
        # 1. News (no AI)
        log("\n[1/4] Collecting news")
        fixture = news.load_fixture(ROOT / args.news_fixture) if args.news_fixture else None
        pools = fixture["pools"] if fixture else news.collect(cfg, log)
        if fixture and "sources" in fixture:
            sources = fixture["sources"]
        else:
            try:
                picks, sources = news.select_sources(pools, plan["recent_news_titles"], log)
            except (ValueError, KeyError, IndexError) as err:
                return finish_fail(f"Not enough news ({err}). Check the feed lines in the log.")
            log(f"  picks: {picks}")

        # 2. AI step 1: articles
        log("\n[2/4] Writing the articles")
        schema_a = articles_schema(plan["opinion"])

        def finish_a(out):
            n: list[str] = []
            a = autofix.fix_articles(out, n)
            crit, more = checks.check_articles(a, plan, sources)
            return a, crit, n + more

        try:
            a, step_notes = run_step("articles", client, prompt("common", "articles"),
                                     plan_articles(plan) + "\n\n" + sources_text(sources),
                                     schema_a, finish_a, repairs)
        except StepFailed as err:
            return finish_fail(str(err), {"step": "articles"})
        except LLMError as err:
            return finish_fail(str(err))
        notes += step_notes
        draft_file.write_text(json.dumps({"sources": [{k: s.get(k) for k in ("id", "role", "source", "title", "url", "thin")}
                                                      for s in sources],
                                          "format": FORMAT, "articles": a, "notes": step_notes,
                                          "fallback_used": bool(getattr(client, "used_fallback", False))},
                                         ensure_ascii=False, indent=1),
                              encoding="utf-8")

    # 3. AI step 2: word bank + lab + other languages
    log("\n[3/4] Writing the Word Bank, Language Lab and Other Languages")
    schema_b = lab_schema(len(a["star_words"]) + len(a["recycled_words"]))

    def finish_b(out):
        n: list[str] = []
        c = autofix.fix_lab({**a, **{k: out[k] for k in out if k in schema_b["properties"]}}, n)
        crit, more = checks.check_lab(c, plan)
        return c, crit, n + more

    try:
        content, step_notes = run_step("lab", client, prompt("common", "lab"),
                                       plan_lab(plan, a), schema_b, finish_b, repairs)
    except StepFailed as err:
        return finish_fail(str(err) + "\n\nThe articles are saved; the next run will only redo this step.",
                           {"step": "lab"})
    except LLMError as err:
        return finish_fail(str(err) + "\n\nThe articles are saved; the next run will only redo this step.")
    notes += step_notes

    # 4. Save + render
    log("\n[4/4] Publishing")
    more, sizes = checks.final_notes(content)
    notes += more
    for n in notes:
        log(f"    · {n}")
    saved = {
        "format": FORMAT, "plan": plan, "sources": [{k: s.get(k) for k in ("id", "role", "source", "title", "url", "thin")} for s in sources],
        "content": content, "sizes": sizes, "notes": notes, "provider": provider, "requests": budget.log,
        "fallback_used": bool(getattr(client, "used_fallback", False)) or draft_fallback,
    }
    data_file.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    draft_file.unlink(missing_ok=True)
    rebuild_site(cfg, e)
    fb = " · ⚠️ written with the fallback model; the backup run will try the primary models again" \
        if saved["fallback_used"] else ""
    SUMMARY.append(f"✅ Published{' (rewritten with the primary model)' if upgrade else ''} · "
                   f"{sizes.get('total')} words · {budget.used} AI request(s){fb}\n\n"
                   f"Cover: {checks.plain(content['cover']['headline'])}")
    SUMMARY.append("\nRequests:\n" + "\n".join(f"- {x}" for x in budget.log))
    if notes:
        SUMMARY.append("\nNotes (published anyway):\n" + "\n".join(f"- {n}" for n in notes))
    write_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
