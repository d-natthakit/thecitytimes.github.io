"""Simulate Gemini problems (quota, busy server, cut-off answers, unknown model)
against the real pipeline code, without using any real quota.

    python tests/fake_gemini_test.py

A tiny local server pretends to be Gemini. Each case lists what each model
answers, in order ("ok", "busy", "daily", ...) and the requests we expect.
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
TEST_DATE = "2026-10-31"          # a Saturday after Issue 1
PLAN: dict[str, list] = {}        # model -> its answers, used in order (then "ok")
HITS: list[str] = []              # "model:answer" for every request received


def answer(kind: str, schema: dict):
    """HTTP status and body that Gemini would send for this kind of answer."""
    if kind == "ok":
        data = json.loads((FIX / f"{schema.get('title', 'articles')}.json").read_text(encoding="utf-8"))
        data = {k: v for k, v in data.items() if k in schema.get("properties", data)}
        return 200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(data)}]}}],
                     "usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 5000}}
    return {
        "cut": (200, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"cov'}]}}]}),
        "daily": (429, {"error": {"message": "Quota exceeded, limit: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}),
        "minute": (429, {"error": {"message": "per minute", "details": [{"retryDelay": "3s"}]}}),
        "busy": (503, {"error": {"message": "This model is currently experiencing high demand."}}),
        "missing": (404, {"error": {"message": "models/x is not found"}}),
    }[kind]


class FakeGemini(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = self.path.split("/models/")[1].split(":")[0]
        queue = PLAN.get(model, [])
        kind = queue.pop(0) if queue else "ok"
        HITS.append(f"{model}:{kind}")
        code, payload = answer(kind, body["generationConfig"].get("responseJsonSchema", {}))
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def run_case(name, answers, expect_exit, expect_hits=None, keep=None, extra=()):
    """Run build.main() in a temporary copy of the repo (or `keep`, to continue a scenario)."""
    global PLAN
    PLAN = {k: list(v) for k, v in answers.items()}
    HITS.clear()
    work = keep or Path(tempfile.mkdtemp())
    if not keep:
        shutil.copytree(REPO, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("docs", "data"))
    sys.argv = ["build.py", "--date", TEST_DATE, "--news-fixture", "tests/fixtures/news.json", *extra]
    import build
    build.ROOT, build.DATA, build.DRAFTS = work, work / "data" / "issues", work / "data" / "drafts"
    build.FAILED, build.DOCS = work / "data" / "failed", work / "docs"
    build.SUMMARY.clear()
    print(f"\n######## {name}")
    code = build.main()
    ok = code == expect_exit and (expect_hits is None or HITS == expect_hits)
    print(f"-> exit {code}, requests: {HITS}  {'PASS' if ok else 'FAIL'}")
    return ok, work


def main():
    srv = HTTPServer(("127.0.0.1", 0), FakeGemini)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ.update({"GEMINI_API_KEY": "test", "PROVIDER": "gemini",
                       "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    sys.path.insert(0, str(REPO / "scripts"))
    import llm
    llm.Gemini.BASE = f"http://127.0.0.1:{srv.server_port}"
    llm.time.sleep = lambda s: None          # do not really wait in tests

    A, B, C = "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"   # primary, primary, fallback
    saved = lambda w: json.loads((w / "data" / "issues" / f"{TEST_DATE}.json").read_text())  # noqa: E731
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
    results.append(ok and saved(work)["fallback_used"])
    results.append(run_case("main run again (no --upgrade) → does nothing", {}, 0, [], keep=work)[0])
    ok, _ = run_case("backup run, primary still busy → keeps the fallback issue (no fallback call)",
                     {A: ["busy"] * 9, B: ["busy"] * 9}, 0, keep=work, extra=["--upgrade"])
    results.append(ok and f"{C}:ok" not in HITS)
    ok, _ = run_case("backup run, primary free → rewrites the issue with the primary model",
                     {}, 0, [f"{A}:ok", f"{A}:ok"], keep=work, extra=["--upgrade"])
    results.append(ok and not saved(work)["fallback_used"])

    results.append(run_case("everything down → fails within the request budget",
                            {m: ["busy"] * 9 for m in (A, B, C)}, 1)[0])
    ok, work = run_case("lab step fails, articles kept as a draft",
                        {A: ["ok", "daily"], B: ["daily"], C: ["daily"]}, 1)
    results.append(ok and (work / "data" / "drafts" / f"{TEST_DATE}.json").exists())
    results.append(run_case("next run reuses the draft → only 1 request", {}, 0, [f"{A}:ok"], keep=work)[0])

    print(f"\n{sum(results)}/{len(results)} cases passed")
    srv.shutdown()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
