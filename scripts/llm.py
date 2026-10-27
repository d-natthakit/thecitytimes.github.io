"""AI clients for Gemini (free tier) and Claude (paid API), plus an offline mock.

Every HTTP request counts against a per-run budget, so one bad morning can
never burn through the free daily quota."""
import json
import os
import re
import time
from pathlib import Path

import requests


class LLMError(RuntimeError):
    pass


class Budget:
    def __init__(self, limit: int):
        self.limit, self.used, self.log = limit, 0, []

    def take(self):
        if self.used >= self.limit:
            raise LLMError(f"request budget for this run is used up ({self.limit} requests)")
        self.used += 1

    def note(self, model: str, outcome: str):
        self.log.append(f"{model}: {outcome}")


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("no JSON in the answer")
        return json.loads(text[start:end + 1])


def _wait_seconds(resp: requests.Response, default: int) -> int:
    m = re.search(r'"retryDelay":\s*"(\d+)', resp.text)
    return min(int(m.group(1)) + 2, 90) if m else default


class Gemini:
    """Primary models are retried with growing waits while Google is busy (HTTP 503),
    up to retry_wait_minutes per run. Only then is the fallback model used."""
    BASE = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")

    def __init__(self, models, budget: Budget, log=print, timeout=240, wait_minutes=15):
        self.key = os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise LLMError("GEMINI_API_KEY is not set.")
        if isinstance(models, dict):
            self.primary, self.fallback = list(models.get("primary", [])), list(models.get("fallback", []))
        else:
            self.primary, self.fallback = list(models), []
        self.budget, self.log, self.timeout = budget, log, timeout
        self.wait_left = wait_minutes * 60
        self.allow_fallback = True
        self.dead = set()                 # out of daily quota or not found: skip for this run
        self.used = []                    # (model, "primary"|"fallback") for each answered step
        self.no_schema = self.no_thinking = False

    # ---- one HTTP request ----
    def _body(self, system, user, schema, temperature):
        gen = {"temperature": temperature, "maxOutputTokens": 65536, "responseMimeType": "application/json"}
        if schema and not self.no_schema:
            gen["responseJsonSchema"] = schema
        if not self.no_thinking:
            gen["thinkingConfig"] = {"thinkingLevel": "low"}
        return {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": gen}

    def _call(self, model, system, user, schema, temperature):
        """Returns (kind, value): ok/data, busy/seconds, skip/reason (try another model), dead/reason."""
        for _ in range(3):                # loop only to drop an option Google rejects
            self.budget.take()
            try:
                r = requests.post(f"{self.BASE}/v1beta/models/{model}:generateContent",
                                  json=self._body(system, user, schema, temperature),
                                  headers={"x-goog-api-key": self.key}, timeout=self.timeout)
            except requests.Timeout:
                return "skip", f"no answer within {self.timeout}s"
            except requests.RequestException as e:
                return "busy", f"network error {e.__class__.__name__}", 30
            body = r.text
            if r.status_code == 400 and "responsejsonschema" in body.lower().replace("_", "") and not self.no_schema:
                self.no_schema = True
                self.log("  ! schema option not accepted; continuing without it")
                continue
            if r.status_code == 400 and "thinking" in body.lower() and not self.no_thinking:
                self.no_thinking = True
                self.log("  ! thinking option not accepted; continuing without it")
                continue
            break
        if r.status_code == 429:
            if re.search(r"PerDay|per day|per_day", body, re.I):
                return "dead", "daily quota used up"
            return "busy", "rate limit (per minute)", _wait_seconds(r, 60)
        if r.status_code in (500, 502, 503, 504):
            return "busy", f"HTTP {r.status_code} high demand", None
        if r.status_code == 404:
            return "dead", "model not found — update models.gemini in config.yml"
        if r.status_code != 200:
            return "skip", f"HTTP {r.status_code} {body[:200]}"
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        reason = cand.get("finishReason")
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or []
                       if not p.get("thought"))
        usage = data.get("usageMetadata", {})
        if reason not in (None, "STOP"):
            return "skip", f"answer stopped early ({reason})"
        try:
            out = parse_json(text)
        except (LLMError, json.JSONDecodeError) as e:
            return "skip", f"answer was not valid JSON ({e})"
        self.log(f"  ok: {model} · in {usage.get('promptTokenCount')} / out {usage.get('candidatesTokenCount')}"
                 f" / thinking {usage.get('thoughtsTokenCount', 0)} tokens")
        return "ok", out

    def _sleep(self, seconds):
        seconds = max(1, min(seconds, self.wait_left))
        self.log(f"  … waiting {seconds}s (wait time left this run: {self.wait_left - seconds}s)")
        time.sleep(seconds)
        self.wait_left -= seconds

    # ---- one AI answer ----
    def json(self, system: str, user: str, schema: dict | None = None, temperature: float = 0.7) -> dict:
        last = "no model available"

        def attempt(model, tier):
            nonlocal last
            res = self._call(model, system, user, schema, temperature)
            kind = res[0]
            if kind == "ok":
                self.budget.note(model, "ok")
                self.used.append((model, tier))
                return res
            last = f"{model}: {res[1]}"
            self.budget.note(model, res[1])
            self.log(f"  ! {last}")
            if kind == "dead":
                self.dead.add(model)
            return res

        # primary models: rotate, waiting longer each time Google is busy
        skipped, k, backoff = set(), 0, 60
        while True:
            pool = [m for m in self.primary if m not in self.dead and m not in skipped]
            if not pool:
                break
            model = pool[k % len(pool)]
            k += 1
            res = attempt(model, "primary")
            if res[0] == "ok":
                return res[1]
            if res[0] == "skip":
                skipped.add(model)
            elif res[0] == "busy":
                if self.wait_left <= 0:
                    break
                self._sleep(res[2] or backoff)
                backoff = min(backoff * 2, 480)

        if not self.allow_fallback:
            raise LLMError(f"primary models unavailable. Last problem: {last}")
        for model in self.fallback:
            if model in self.dead:
                continue
            for retry in range(2):
                res = attempt(model, "fallback")
                if res[0] == "ok":
                    return res[1]
                if res[0] != "busy" or retry:
                    break
                time.sleep(30)
        raise LLMError(f"Gemini could not answer. Last problem: {last}")

    @property
    def used_fallback(self) -> bool:
        return any(tier == "fallback" for _, tier in self.used)


class Anthropic:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, models, budget: Budget, log=print):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        self.models, self.budget, self.log = models, budget, log

    def json(self, system: str, user: str, schema: dict | None = None, temperature: float = 0.7) -> dict:
        if schema:
            user += ("\n\n# OUTPUT\nReturn only one JSON object that matches this JSON Schema, "
                     "with no text before or after it:\n" + json.dumps(schema, ensure_ascii=False))
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        last = None
        for model in self.models:
            for _ in range(2):
                self.budget.take()
                try:
                    r = requests.post(self.URL, headers=headers, timeout=600, json={
                        "model": model, "max_tokens": 16000, "temperature": temperature, "system": system,
                        "messages": [{"role": "user", "content": user}]})
                except requests.RequestException as e:
                    last = f"{model}: {e.__class__.__name__}"
                    self.budget.note(model, "network error")
                    continue
                if r.status_code in (429, 500, 502, 503, 529):
                    last = f"{model}: HTTP {r.status_code}"
                    self.budget.note(model, last)
                    self.log(f"  ! {last}")
                    time.sleep(30)
                    continue
                if r.status_code != 200:
                    last = f"{model}: HTTP {r.status_code} {r.text[:300]}"
                    self.budget.note(model, f"HTTP {r.status_code}")
                    self.log(f"  ! {last}")
                    break
                text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
                try:
                    out = parse_json(text)
                except (LLMError, json.JSONDecodeError) as e:
                    last = f"{model}: bad JSON ({e})"
                    self.budget.note(model, "bad JSON")
                    break
                self.budget.note(model, "ok")
                self.log(f"  ok: {model}")
                return out
        raise LLMError(f"Claude could not answer. Last problem: {last}")


class Mock:
    """Offline provider for tests: answers from tests/fixtures/<task>.json."""

    def __init__(self, fixture_dir: Path, budget: Budget, log=print):
        self.dir, self.budget, self.log = fixture_dir, budget, log

    def json(self, system: str, user: str, schema: dict | None = None, temperature: float = 0.7) -> dict:
        self.budget.take()
        task = (schema or {}).get("title", "articles")
        self.budget.note("mock", task)
        return json.loads((self.dir / f"{task}.json").read_text(encoding="utf-8"))


def make_client(provider: str, cfg: dict, root: Path, budget: Budget, log=print):
    if provider == "gemini":
        return Gemini(cfg["models"]["gemini"], budget, log, cfg.get("request_timeout", 240),
                      cfg.get("retry_wait_minutes", 15))
    if provider == "anthropic":
        return Anthropic(cfg["models"]["anthropic"], budget, log)
    if provider == "mock":
        return Mock(root / "tests" / "fixtures", budget, log)
    raise LLMError(f"Unknown provider: {provider}")


# ---------- JSON schemas ----------

def _s(desc=None):
    return {"type": "string", **({"description": desc} if desc else {})}


def _arr(item, n=None):
    a = {"type": "array", "items": item}
    if n:
        a["minItems"] = a["maxItems"] = n
    return a


def _obj(props: dict, title=None):
    o = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    if title:
        o["title"] = title
    return o


def articles_schema(opinion: str) -> dict:
    article = _obj({"headline": _s(), "paragraphs": _arr(_s()), "source_ids": _arr(_s("e.g. S1"))})
    props = {
        "quick_route": _arr(_s(), 3),
        "star_words": _arr(_s(), 8),
        "recycled_words": _arr(_s(), 4),
        "cover": article,
        "world": _arr(article, 2),
        "thailand": _arr(article, 2),
        "feature_a": article,
        "feature_b": article,
    }
    if opinion == "two_takes":
        props["two_takes"] = _obj({
            "question": _s(),
            "view_a": _obj({"title": _s(), "text": _s()}),
            "view_b": _obj({"title": _s(), "text": _s()}),
            "phrases": _arr(_obj({"phrase": _s(), "function": _s()}), 3),
        })
    else:
        props["say_it_better"] = _obj({
            "situation_title": _s(), "situation": _s(),
            "formal": _s(), "neutral": _s(), "casual": _s(),
            "useful_phrases": _arr(_s(), 3),
            "avoid": _obj({"phrase": _s(), "reason": _s()}),
        })
    return _obj(props, "articles")


def lab_schema(glossary_size: int = 12) -> dict:
    return _obj({
        "word_workshop": _arr(_obj({"word": _s(), "collocations": _arr(_s(), 3), "example": _s()}), 3),
        "activity": _obj({"instructions": _s(), "items": _arr(_s(), 6), "answers": _arr(_s(), 6)}),
        "errors": _arr(_obj({"sentence": _s(), "corrected": _s(), "reason": _s()}), 5),
        "trap": _obj({"title": _s(), "wrong": _s(), "right": _s(), "explanation": _s()}),
        "other_language": _obj({
            "headline": _s(), "note": _s(),
            "phrases": _arr(_obj({"phrase": _s(), "pronunciation": _s(), "meaning": _s()}), 3),
            "borrowed": _obj({"word": _s(), "note": _s()}),
        }),
        "glossary": _arr(_obj({"word": _s(), "pos": _s("part of speech"), "definition": _s(),
                               "thai": _s("Thai translation")}), glossary_size),
    }, "lab")
