"""Build today's issue of THE BKK GEM POST.

    python scripts/build.py                 # today's issue (Bangkok time)
    python scripts/build.py --date 2026-10-28 --force
    PROVIDER=mock python scripts/build.py --news-fixture tests/fixtures/news.json

Steps: plan (code) → news (RSS + code picks stories) → AI step 1: articles →
code auto-fix + checks (one repair if critical) → AI step 2: lab + glossary →
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
        f"Feature A theme: {plan['feature_a']}",
        f"Feature B theme: {plan['feature_b']}",
        f"Opinion slot: {plan['opinion']} (fill only this one)",
    ]
    if plan["opinion"] == "say_it_better":
        lines.append(f"Say It Better setting: {plan['setting']}")
    lines.append("Avoid these as ★ words: " + (", ".join(plan["avoid_star_words"]) or "none"))
    if plan["review_pool"]:
        pool = ", ".join(r["word"] for r in plan["review_pool"])
        lines.append(f"↺ review candidates (use 4 that fit naturally): {pool}")
    else:
        lines.append("↺ review candidates: none yet. Choose 4 high-frequency B2 words.")
    for label, key in (("Opinion", "recent_opinions"), ("Features", "recent_features")):
        if plan[key]:
            lines.append(f"Recent {label} topics — do not repeat: " + " | ".join(plan[key][:14]))
    return "\n".join(lines)


def plan_lab(plan: dict, a: dict) -> str:
    lines = [
        "# TODAY'S PLAN",
        f"Language Lab activity: {plan['activity']}",
        f"Thai Speaker's Trap category: {plan['trap']}",
        f"Other Languages: {plan['language']}",
        "★ words (in this order): " + " | ".join(a["star_words"]),
        "↺ words (in this order): " + " | ".join(a["recycled_words"]),
        f"Glossary entries needed: {len(a['star_words']) + len(a['recycled_words'])}",
    ]
    if plan["recent_traps"]:
        lines.append("Recent Thai Speaker's Traps — do not repeat: " + " | ".join(plan["recent_traps"][:14]))
    if plan["recent_language_notes"]:
        lines.append(f"Recent {plan['language']} notes — do not repeat: " + " | ".join(plan["recent_language_notes"]))
    return "\n".join(lines)


def sources_text(sources: list[dict]) -> str:
    out = ["# SOURCES"]
    for s in sources:
        out += [f"## {s['id']} — story: {s['role']} — {s['source']} — {s['title']}", s["text"], ""]
    return "\n".join(out)


def articles_text(a: dict) -> str:
    out = ["# TODAY'S ARTICLES"]
    for name, text in checks.article_blocks(a).items():
        out += [f"## {name}", text, ""]
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
                                         d["_prev"], "../"), encoding="utf-8")
    latest = issues[-1]
    (DOCS / "index.html").write_text(render.issue_page(
        e, paper, latest["plan"], latest["content"], latest["sizes"], latest["sources"], latest["_prev"], ""),
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
    data_file = DATA / f"{day.isoformat()}.json"
    draft_file = DRAFTS / f"{day.isoformat()}.json"
    upgrade = False
    if data_file.exists() and not args.force:
        existing = json.loads(data_file.read_text(encoding="utf-8"))
        if not existing.get("fallback_used"):
            log(f"Issue for {day} already exists. Use --force to rebuild it.")
            return 0
        upgrade = True
        log(f"Issue for {day} was written by the fallback model: trying again with the primary models.\n"
            "If they are still busy, the current issue stays online.")

    plan = make_plan(cfg, day, DATA)
    log(f"== {cfg['paper_name']} · Issue {plan['issue_no']} · {plan['date_long']}")
    log(f"   {plan['weekday']}: {plan['feature_a']} / {plan['feature_b']} / {plan['opinion']} / "
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
    if draft_file.exists() and not args.force and not upgrade:
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
        try:
            picks = news.pick_stories(pools)
        except (ValueError, KeyError, IndexError) as err:
            return finish_fail(f"Not enough news ({err}). Check the feed lines in the log.")
        log(f"  picks: {picks}")
        sources = fixture["sources"] if fixture and "sources" in fixture else news.gather_sources(pools, picks, log)

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
        draft_file.write_text(json.dumps({"sources": [{k: s[k] for k in ("id", "role", "source", "title", "url")}
                                                      for s in sources],
                                          "articles": a, "notes": step_notes,
                                          "fallback_used": bool(getattr(client, "used_fallback", False))},
                                         ensure_ascii=False, indent=1),
                              encoding="utf-8")

    # 3. AI step 2: lab + glossary
    log("\n[3/4] Writing the Language Lab and Glossary")
    schema_b = lab_schema(len(a["star_words"]) + len(a["recycled_words"]))

    def finish_b(out):
        n: list[str] = []
        c = autofix.fix_lab({**a, **{k: out[k] for k in out if k in schema_b["properties"]}}, n)
        crit, more = checks.check_lab(c, plan)
        return c, crit, n + more

    try:
        content, step_notes = run_step("lab", client, prompt("common", "lab"),
                                       plan_lab(plan, a) + "\n\n" + articles_text(a), schema_b, finish_b, repairs)
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
        "plan": plan, "sources": [{k: s[k] for k in ("id", "role", "source", "title", "url")} for s in sources],
        "content": content, "sizes": sizes, "notes": notes, "provider": provider, "requests": budget.log,
        "fallback_used": bool(getattr(client, "used_fallback", False)) or draft_fallback,
    }
    data_file.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    draft_file.unlink(missing_ok=True)
    rebuild_site(cfg, e)
    fb = " · ⚠️ written with the fallback model; the 06:27 run will try the primary models again" \
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
