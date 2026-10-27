"""Build today's issue of THE BKK GEM POST.

    python scripts/build.py                 # today's issue (Bangkok time)
    python scripts/build.py --date 2026-10-28 --force
    PROVIDER=mock python scripts/build.py --news-fixture tests/fixtures/news.json --lenient
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml

import checks
import news
import render
from llm import PICKS_SCHEMA, LLMError, content_schema, make_client
from plan import make_plan, today_in

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "issues"
DOCS = ROOT / "docs"
SUMMARY = []


def log(msg=""):
    print(msg, flush=True)


def summary(msg=""):
    SUMMARY.append(msg)


def write_summary():
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and SUMMARY:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(SUMMARY) + "\n")


def plan_text(plan: dict) -> str:
    lines = [
        "# TODAY'S PLAN",
        f"Date: {plan['date_long']} (Issue {plan['issue_no']})",
        f"Feature A theme: {plan['feature_a']}",
        f"Feature B theme: {plan['feature_b']}",
        f"Opinion slot: {plan['opinion']} (fill only this one)",
    ]
    if plan["opinion"] == "say_it_better":
        lines.append(f"Say It Better setting: {plan['setting']}")
    lines += [
        f"Language Lab activity: {plan['activity']}",
        f"Thai Speaker's Trap category: {plan['trap']}",
        f"Other Languages: {plan['language']}",
        "Avoid these as ★ words (used in the last 30 days): " + (", ".join(plan["avoid_star_words"]) or "none"),
    ]
    if plan["review_pool"]:
        pool = ", ".join(f"{r['word']} (Issue {r['taught_issue']}, {r['days_ago']} days ago)" for r in plan["review_pool"])
        lines.append(f"↺ review candidates — choose 4 that fit naturally: {pool}")
        if len(plan["review_pool"]) < 4:
            lines.append("Fewer than 4 candidates: add high-frequency B2 words to make 4.")
    else:
        lines.append("↺ review candidates: none yet. Choose 4 high-frequency B2 words.")
    for label, key in (("Thai Speaker's Trap", "recent_traps"), ("Opinion", "recent_opinions"),
                       (f"Other Languages ({plan['language']})", "recent_language_notes"),
                       ("Features", "recent_features")):
        if plan[key]:
            lines.append(f"Recent {label} topics — do not repeat: " + " | ".join(plan[key]))
    return "\n".join(lines)


def sources_text(sources: list[dict]) -> str:
    out = ["# SOURCES"]
    for s in sources:
        out += [f"## {s['id']} — story: {s['role']} — {s['source']} — {s['title']}", f"URL: {s['url']}", s["text"], ""]
    return "\n".join(out)


def valid_picks(p: dict, pools: dict) -> dict | None:
    w = {i["id"] for i in pools["world"]}
    t = {i["id"] for i in pools["thailand"]}
    try:
        cover, world, thai = p["cover"], list(p["world"]), list(p["thailand"])
    except (KeyError, TypeError):
        return None
    chosen = [cover, *world, *thai]
    if cover not in w | t or len(world) != 2 or len(thai) != 2 or len(set(chosen)) != 5:
        return None
    if not set(world) <= w or not set(thai) <= t:
        return None
    return {"cover": cover, "world": world, "thailand": thai}


def fallback_picks(pools: dict) -> dict:
    w = [i["id"] for i in pools["world"]]
    t = [i["id"] for i in pools["thailand"]]
    return {"cover": w[0], "world": w[1:3], "thailand": t[:2]}


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
        prev = issues[i - 1]["plan"]["date"] if i else None
        d["_prev"] = prev
        out = DOCS / "issues" / f"{d['plan']['date']}.html"
        out.write_text(render.issue_page(e, paper, d["plan"], d["content"], d["sizes"], d["sources"], prev, "../"),
                       encoding="utf-8")
    latest = issues[-1]
    (DOCS / "index.html").write_text(
        render.issue_page(e, paper, latest["plan"], latest["content"], latest["sizes"], latest["sources"],
                          latest["_prev"], ""), encoding="utf-8")
    listing = [{"issue_no": d["plan"]["issue_no"], "date": d["plan"]["date"], "date_long": d["plan"]["date_long"],
                "headline": checks.plain(d["content"]["cover"]["headline"])} for d in reversed(issues)]
    (DOCS / "archive.html").write_text(render.archive_page(e, paper, listing), encoding="utf-8")
    log(f"Site rebuilt: {len(issues)} issue(s), latest {latest['plan']['date']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in Bangkok)")
    ap.add_argument("--force", action="store_true", help="rebuild even if the issue exists")
    ap.add_argument("--provider", help="gemini | anthropic | mock")
    ap.add_argument("--news-fixture", help="offline news JSON for testing")
    ap.add_argument("--lenient", action="store_true", help="publish even if hard checks still fail")
    ap.add_argument("--site-only", action="store_true", help="only rebuild HTML from saved issues")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))
    DATA.mkdir(parents=True, exist_ok=True)
    (DOCS / "issues").mkdir(parents=True, exist_ok=True)
    (DOCS / ".nojekyll").touch()
    e = render.env(ROOT / "templates")

    if args.site_only:
        rebuild_site(cfg, e)
        return 0

    day = date.fromisoformat(args.date) if args.date else today_in(cfg["timezone"])
    data_file = DATA / f"{day.isoformat()}.json"
    if data_file.exists() and not args.force:
        log(f"Issue for {day} already exists. Use --force to rebuild it.")
        return 0

    plan = make_plan(cfg, day, DATA)
    log(f"== {cfg['paper_name']} · Issue {plan['issue_no']} · {plan['date_long']}")
    log(f"   {plan['weekday']}: {plan['feature_a']} / {plan['feature_b']} / {plan['opinion']} / "
        f"{plan['activity']} / {plan['trap']} / {plan['language']}")
    log(f"   review candidates: {len(plan['review_pool'])}, words to avoid: {len(plan['avoid_star_words'])}")
    summary(f"## Issue {plan['issue_no']} · {plan['date_long']}")

    provider = args.provider or os.environ.get("PROVIDER") or cfg.get("provider", "gemini")
    try:
        client = make_client(provider, cfg, ROOT, log)
    except LLMError as err:
        log(f"ERROR: {err}")
        summary(f"❌ {err}")
        write_summary()
        return 1
    log(f"Provider: {provider}")

    # 1. News
    log("\n[1/4] Collecting news")
    fixture = news.load_fixture(ROOT / args.news_fixture) if args.news_fixture else None
    pools = fixture["pools"] if fixture else news.collect(cfg, log)
    if len(pools.get("world", [])) < 3 or len(pools.get("thailand", [])) < 2:
        msg = f"Not enough news: {len(pools.get('world', []))} world, {len(pools.get('thailand', []))} Thai stories."
        log("ERROR: " + msg)
        summary("❌ " + msg)
        write_summary()
        return 1

    log("\n[2/4] Choosing stories")
    select_prompt = (ROOT / "prompts" / "select.md").read_text(encoding="utf-8")
    try:
        raw = client.json(select_prompt, f"Today is {plan['date_long']}.\n\n" + news.candidates_text(pools),
                          PICKS_SCHEMA, temperature=0.3)
        picks = valid_picks(raw, pools)
    except LLMError as err:
        log(f"  ! {err}")
        picks = None
    if not picks:
        log("  ! AI choice was not usable; using the most widely reported stories")
        picks = fallback_picks(pools)
    log(f"  picks: {picks}")
    sources = fixture["sources"] if fixture and "sources" in fixture else news.gather_sources(pools, picks, log)

    # 2. Write + check + repair
    log("\n[3/4] Writing the issue")
    system = (ROOT / "prompts" / "writer.md").read_text(encoding="utf-8")
    user = plan_text(plan) + "\n\n" + sources_text(sources)
    schema = content_schema(plan["opinion"])
    try:
        content = client.json(system, user, schema)
    except LLMError as err:
        log(f"ERROR: {err}")
        summary(f"❌ {err}")
        write_summary()
        return 1

    hard, soft, sizes = [], [], {}
    for attempt in range(cfg.get("max_repairs", 2) + 1):
        hard, soft, sizes = checks.run(content, plan, sources)
        log(f"  check {attempt + 1}: {len(hard)} problem(s) to fix, {len(soft)} note(s), "
            f"{sizes.get('total', '?')} words")
        for h in hard:
            log(f"    ✗ {h}")
        if not hard or attempt == cfg.get("max_repairs", 2):
            break
        repair = (user + "\n\n# YOUR PREVIOUS ANSWER\n" + json.dumps(content, ensure_ascii=False) +
                  "\n\n# PROBLEMS TO FIX\n" + "\n".join(f"- {p}" for p in hard + soft) +
                  "\n\nReturn the complete corrected JSON. Keep everything that is already correct.")
        try:
            content = client.json(system, repair, schema, temperature=0.4)
        except LLMError as err:
            log(f"  ! repair failed: {err}")
            break
    for s in soft:
        log(f"    · {s}")

    if hard and not args.lenient:
        fail = ROOT / "data" / "failed"
        fail.mkdir(exist_ok=True)
        (fail / f"{day.isoformat()}.json").write_text(json.dumps(content, ensure_ascii=False, indent=1), "utf-8")
        log("ERROR: the issue still has problems after repairs, so it was not published.")
        summary("❌ Not published. Problems:\n" + "\n".join(f"- {h}" for h in hard))
        write_summary()
        return 1

    # 3. Save + render
    log("\n[4/4] Publishing")
    saved = {
        "plan": plan,
        "sources": [{k: s[k] for k in ("id", "role", "source", "title", "url")} for s in sources],
        "content": content, "sizes": sizes, "checks": {"hard": hard, "soft": soft},
        "provider": provider,
    }
    data_file.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    rebuild_site(cfg, e)
    summary(f"✅ Published · {sizes.get('total')} words · cover: {checks.plain(content['cover']['headline'])}")
    if soft:
        summary("Notes:\n" + "\n".join(f"- {s}" for s in soft))
    write_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
