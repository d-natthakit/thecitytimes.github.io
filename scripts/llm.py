"""AI clients: Gemini (free tier), Claude (paid API) and an offline Mock.

Every client has one method, `json(system, user, schema)`, that returns a dict.
Every HTTP request counts against a per-run Budget, so one bad morning can
never use up the free daily quota.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import NamedTuple

import requests


class LLMError(RuntimeError):
    """The AI could not give a usable answer."""


class Budget:
    """Counts AI requests in this run and keeps a short log for the run summary."""

    def __init__(self, limit: int):
        self.limit, self.used, self.log = limit, 0, []

    def take(self):
        if self.used >= self.limit:
            raise LLMError(f"request budget for this run is used up ({self.limit} requests)")
        self.used += 1

    def note(self, model: str, outcome: str):
        self.log.append(f"{model}: {outcome}")


def parse_json(text: str) -> dict:
    """Read a JSON object from an AI answer, tolerating ``` fences or extra text."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("no JSON in the answer")
        return json.loads(text[start:end + 1])


# ---------- Gemini ----------

class Result(NamedTuple):
    kind: str                 # ok | busy (wait and retry) | skip (try another model) | dead (skip all run)
    info: str = ""
    wait: int | None = None   # seconds suggested by Google, for "busy"
    data: dict | None = None


class Gemini:
    """Free-tier Gemini.

    Primary models take turns. While Google answers 503/429 ("busy"), the client
    waits longer each time (1, 2, 4, 8 min) up to `retry_wait_minutes` per run.
    Only then is the fallback model used. A model whose daily quota is gone or
    whose name does not exist is skipped for the rest of the run.
    """
    BASE = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")

    def __init__(self, models: dict | list, budget: Budget, log=print, timeout=240, wait_minutes=15):
        self.key = os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise LLMError("GEMINI_API_KEY is not set.")
        if isinstance(models, dict):
            self.primary, self.fallback = list(models.get("primary", [])), list(models.get("fallback", []))
        else:
            self.primary, self.fallback = list(models), []
        self.budget, self.log, self.timeout = budget, log, timeout
        self.wait_left = wait_minutes * 60
        self.allow_fallback = True        # False in the backup "upgrade" run
        self.dead: set[str] = set()
        self.used: list[tuple[str, str]] = []   # (model, "primary" | "fallback") per answer
        self.no_schema = self.no_thinking = False

    @property
    def used_fallback(self) -> bool:
        return any(tier == "fallback" for _, tier in self.used)

    def _body(self, system, user, schema, temperature) -> dict:
        gen = {"temperature": temperature, "maxOutputTokens": 65536, "responseMimeType": "application/json"}
        if schema and not self.no_schema:
            gen["responseJsonSchema"] = schema
        if not self.no_thinking:
            gen["thinkingConfig"] = {"thinkingLevel": "low"}   # less thinking = faster, fewer cut-off answers
        return {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": gen}

    def _call(self, model, system, user, schema, temperature) -> Result:
        """One request to one model, translated into a Result."""
        for _ in range(3):   # repeats only if Google rejects an optional setting
            self.budget.take()
            try:
                r = requests.post(f"{self.BASE}/v1beta/models/{model}:generateContent",
                                  json=self._body(system, user, schema, temperature),
                                  headers={"x-goog-api-key": self.key}, timeout=self.timeout)
            except requests.Timeout:
                return Result("skip", f"no answer within {self.timeout}s")
            except requests.RequestException as e:
                return Result("busy", f"network error {e.__class__.__name__}", 30)
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
                return Result("dead", "daily quota used up")
            m = re.search(r'"retryDelay":\s*"(\d+)', body)
            return Result("busy", "rate limit (per minute)", min(int(m.group(1)) + 2, 90) if m else 60)
        if r.status_code in (500, 502, 503, 504):
            return Result("busy", f"HTTP {r.status_code} high demand")
        if r.status_code == 404:
            return Result("dead", "model not found — update models.gemini in config.yml")
        if r.status_code != 200:
            return Result("skip", f"HTTP {r.status_code} {body[:200]}")

        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        if cand.get("finishReason") not in (None, "STOP"):
            return Result("skip", f"answer stopped early ({cand.get('finishReason')})")
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        try:
            out = parse_json(text)
        except (LLMError, json.JSONDecodeError) as e:
            return Result("skip", f"answer was not valid JSON ({e})")
        u = data.get("usageMetadata", {})
        self.log(f"  ok: {model} · in {u.get('promptTokenCount')} / out {u.get('candidatesTokenCount')}"
                 f" / thinking {u.get('thoughtsTokenCount', 0)} tokens")
        return Result("ok", data=out)

    def _attempt(self, model, tier, *args) -> Result:
        res = self._call(model, *args)
        if res.kind == "ok":
            self.used.append((model, tier))
            self.budget.note(model, "ok")
        else:
            self.budget.note(model, res.info)
            self.log(f"  ! {model}: {res.info}")
            if res.kind == "dead":
                self.dead.add(model)
        return res

    def _sleep(self, seconds: int):
        seconds = max(1, min(seconds, self.wait_left))
        self.log(f"  … waiting {seconds}s (wait time left this run: {self.wait_left - seconds}s)")
        time.sleep(seconds)
        self.wait_left -= seconds

    def json(self, system: str, user: str, schema: dict | None = None, temperature: float = 0.7) -> dict:
        args = (system, user, schema, temperature)
        last = "no model available"

        # 1) primary models take turns; wait longer each time Google is busy
        skipped, turn, backoff = set(), 0, 60
        while True:
            pool = [m for m in self.primary if m not in self.dead and m not in skipped]
            if not pool:
                break
            model = pool[turn % len(pool)]
            turn += 1
            res = self._attempt(model, "primary", *args)
            if res.kind == "ok":
                return res.data
            last = f"{model}: {res.info}"
            if res.kind == "skip":
                skipped.add(model)
            elif res.kind == "busy":
                if self.wait_left <= 0:
                    break
                self._sleep(res.wait or backoff)
                backoff = min(backoff * 2, 480)

        # 2) fallback model(s): one retry after 30 s if busy
        if not self.allow_fallback:
            raise LLMError(f"primary models unavailable. Last problem: {last}")
        for model in (m for m in self.fallback if m not in self.dead):
            for retry in range(2):
                res = self._attempt(model, "fallback", *args)
                if res.kind == "ok":
                    return res.data
                last = f"{model}: {res.info}"
                if res.kind != "busy" or retry:
                    break
                time.sleep(30)
        raise LLMError(f"Gemini could not answer. Last problem: {last}")


# ---------- Claude (paid) ----------

class Anthropic:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, models: list, budget: Budget, log=print):
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


# ---------- offline tests ----------

class Mock:
    """Answers from tests/fixtures/<schema title>.json (articles.json, lab.json)."""

    def __init__(self, fixture_dir: Path, budget: Budget, log=print):
        self.dir, self.budget, self.log = fixture_dir, budget, log

    def json(self, system: str, user: str, schema: dict | None = None, temperature: float = 0.7) -> dict:
        self.budget.take()
        task = (schema or {}).get("title", "articles")
        self.budget.note("mock", task)
        answer = json.loads((self.dir / f"{task}.json").read_text(encoding="utf-8"))
        wanted = (schema or {}).get("properties", answer)
        return {k: v for k, v in answer.items() if k in wanted}   # e.g. only today's Real English format


def make_client(provider: str, cfg: dict, root: Path, budget: Budget, log=print):
    if provider == "gemini":
        return Gemini(cfg["models"]["gemini"], budget, log, cfg.get("request_timeout", 240),
                      cfg.get("retry_wait_minutes", 15))
    if provider == "anthropic":
        return Anthropic(cfg["models"]["anthropic"], budget, log)
    if provider == "mock":
        return Mock(root / "tests" / "fixtures", budget, log)
    raise LLMError(f"Unknown provider: {provider}")


# ---------- JSON schemas for the two AI steps ----------

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
    """Step 1: news, feature, Real English (one format) + trap, and the day's words."""
    article = _obj({"headline": _s(), "paragraphs": _arr(_s()), "source_ids": _arr(_s("e.g. S1"))})
    props = {
        "star_words": _arr(_s(), 8),
        "recycled_words": _arr(_s(), 4),
        "cover": article,
        "world": _arr(article, 2),
        "thailand": _arr(article, 2),
        "feature": article,
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
        })
    props["trap"] = _obj({"title": _s(), "wrong": _s(), "right": _s(), "explanation": _s()})
    return _obj(props, "articles")


def lab_schema(n_words: int = 12) -> dict:
    """Step 2: word bank (one entry per ★/↺ word), Language Lab, Other Languages."""
    return _obj({
        "word_bank": _arr(_obj({"word": _s(), "pos": _s("part of speech"), "definition": _s(),
                                "thai": _s("Thai translation"), "collocations": _arr(_s())}), n_words),
        "activity": _obj({"instructions": _s(), "items": _arr(_s(), 6), "answers": _arr(_s(), 6)}),
        "errors": _arr(_obj({"sentence": _s(), "corrected": _s(), "reason": _s()}), 5),
        "other_language": _obj({
            "theme_intro": _s(),
            "phrases": _arr(_obj({"phrase": _s(), "pronunciation": _s(), "meaning": _s()}), 3),
            "root": _obj({"root": _s(), "meaning": _s(), "english_words": _s(), "note": _s()}),
            "borrowed": _obj({"word": _s(), "note": _s()}),
            "curious": _obj({"word": _s(), "pronunciation": _s(), "meaning": _s(), "note": _s()}),
        }),
    }, "lab")
