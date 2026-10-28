"""Simulate Gemini failures (quota, busy server, cut-off answers, unknown model)
against the real pipeline code, without using any real quota.

    python tests/fake_gemini_test.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tests" / "fixtures"
PLAN: dict[str, list] = {}       # model -> list of behaviours, consumed in order
HITS: list[str] = []


def answer(kind: str, title: str):
    if kind == "ok":
        text = (FIX / f"{title}.json").read_text(encoding="utf-8")
        return 200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}],
                     "usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 5000}}
    if kind == "cut":
        return 200, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"quick'}]}}]}
    if kind == "daily":
        return 429, {"error": {"message": "Quota exceeded for metric generate_content_free_tier_requests, "
                                          "limit: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}
    if kind == "minute":
        return 429, {"error": {"message": "per minute", "details": [{"retryDelay": "3s"}]}}
    if kind == "busy":
        return 503, {"error": {"message": "The model is overloaded."}}
    if kind == "missing":
        return 404, {"error": {"message": "models/x is not found"}}
    raise ValueError(kind)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = self.path.split("/models/")[1].split(":")[0]
        title = body["generationConfig"].get("responseJsonSchema", {}).get("title", "articles")
        queue = PLAN.get(model, [])
        kind = queue.pop(0) if queue else "ok"
        HITS.append(f"{model}:{kind}")
        code, payload = answer(kind, title)
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def run_case(name, plan, expect_exit, expect_hits=None, keep=None, extra=()):
    global PLAN
    PLAN = {k: list(v) for k, v in plan.items()}
    HITS.clear()
    work = keep or Path(tempfile.mkdtemp())
    if not keep:
        shutil.copytree(REPO, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("docs", "data"))
    sys.argv = ["build.py", "--date", "2026-10-27", "--news-fixture", "tests/fixtures/news.json", *extra]
    import build
    build.ROOT, build.DATA = work, work / "data" / "issues"
    build.DRAFTS, build.DOCS = work / "data" / "drafts", work / "docs"
    build.SUMMARY.clear()
    print(f"\n######## {name}")
    code = build.main()
    ok = code == expect_exit and (expect_hits is None or HITS == expect_hits)
    print(f"-> exit {code}, requests: {HITS}  {'PASS' if ok else 'FAIL'}")
    return ok, work


def main():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["GEMINI_BASE_URL"] = f"http://127.0.0.1:{srv.server_port}"
    os.environ["GEMINI_API_KEY"] = "test"
    os.environ["PROVIDER"] = "gemini"
    sys.path.insert(0, str(REPO / "scripts"))
    import llm
    llm.Gemini.BASE = os.environ["GEMINI_BASE_URL"]
    llm.time.sleep = lambda s: None          # do not really wait in tests

    A, B, C = "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"   # primary, primary, fallback
    results = [
        run_case("normal day: 2 requests", {}, 0, [f"{A}:ok", f"{A}:ok"])[0],
        run_case("first model out of daily quota → skipped for the whole run",
                 {A: ["daily"]}, 0, [f"{A}:daily", f"{B}:ok", f"{B}:ok"])[0],
        run_case("503 high demand once → wait, then the other primary model",
                 {A: ["busy"]}, 0, [f"{A}:busy", f"{B}:ok", f"{A}:ok"])[0],
        run_case("per-minute limit → wait, then the other primary model",
                 {A: ["minute"]}, 0, [f"{A}:minute", f"{B}:ok", f"{A}:ok"])[0],
        run_case("answer cut off (MAX_TOKENS) → other primary model",
                 {A: ["cut"]}, 0, [f"{A}:cut", f"{B}:ok", f"{A}:ok"])[0],
        run_case("model name not found → skipped for the whole run",
                 {A: ["missing"]}, 0, [f"{A}:missing", f"{B}:ok", f"{B}:ok"])[0],
    ]
    ok, work = run_case("primary busy for 15+ minutes → fallback model, issue marked",
                        {A: ["busy"] * 9, B: ["busy"] * 9}, 0,
                        [f"{A}:busy", f"{B}:busy", f"{A}:busy", f"{B}:busy", f"{A}:busy", f"{C}:ok",
                         f"{A}:busy", f"{C}:ok"])
    saved = json.loads((work / "data" / "issues" / "2026-10-27.json").read_text())
    results.append(ok and saved["fallback_used"])
    ok, _ = run_case("main run again (no --upgrade) → does nothing", {}, 0, [], keep=work)
    results.append(ok)
    ok, _ = run_case("backup run, primary still busy → keeps the fallback issue (no fallback call)",
                     {A: ["busy"] * 9, B: ["busy"] * 9}, 0, keep=work, extra=["--upgrade"])
    results.append(ok and not HITS.count(f"{C}:ok"))
    ok, _ = run_case("backup run, primary free → rewrites the issue with the primary model",
                     {}, 0, [f"{A}:ok", f"{A}:ok"], keep=work, extra=["--upgrade"])
    saved = json.loads((work / "data" / "issues" / "2026-10-27.json").read_text())
    results.append(ok and not saved["fallback_used"])
    results.append(run_case("everything down → fails within the request budget",
                            {m: ["busy"] * 9 for m in (A, B, C)}, 1)[0])
    ok, work = run_case("lab step fails, articles kept as a draft",
                        {A: ["ok", "daily"], B: ["daily"], C: ["daily"]}, 1)
    results.append(ok and (work / "data" / "drafts" / "2026-10-27.json").exists())
    ok, _ = run_case("next run reuses the draft → only 1 request", {}, 0, [f"{A}:ok"], keep=work)
    results.append(ok)
    print(f"\n{sum(results)}/{len(results)} cases passed")
    srv.shutdown()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
