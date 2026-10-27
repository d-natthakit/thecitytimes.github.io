"""Thin clients for Gemini (free tier) and Claude (paid API), plus an offline mock."""
import json
import os
import re
import time
from pathlib import Path

import requests


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("The AI did not return JSON.")
        return json.loads(text[start:end + 1])


def _retry_wait(attempt: int) -> int:
    return [10, 30, 60][min(attempt, 2)]


class Gemini:
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, models, log=print):
        self.key = os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise LLMError("GEMINI_API_KEY is not set.")
        self.models, self.log = models, log

    def json(self, system: str, user: str, schema: dict | None = None, temperature: float = 0.7) -> dict:
        gen = {"temperature": temperature, "maxOutputTokens": 32768, "responseMimeType": "application/json"}
        if schema:
            gen["responseJsonSchema"] = schema
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        last = None
        for model in self.models:
            for attempt in range(3):
                try:
                    r = requests.post(self.URL.format(model=model), json=body, timeout=300,
                                      headers={"x-goog-api-key": self.key})
                except requests.RequestException as e:
                    last = f"{model}: {e}"
                    time.sleep(_retry_wait(attempt))
                    continue
                if r.status_code == 400 and "responseJsonSchema" in r.text and "responseJsonSchema" in gen:
                    self.log("  ! schema not accepted, retrying without it")
                    gen.pop("responseJsonSchema")
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"{model}: HTTP {r.status_code} {r.text[:200]}"
                    self.log(f"  ! {last}")
                    if r.status_code == 429 and "PerDay" in r.text:
                        break  # daily quota gone for this model: try the next one
                    time.sleep(_retry_wait(attempt))
                    continue
                if r.status_code != 200:
                    last = f"{model}: HTTP {r.status_code} {r.text[:300]}"
                    self.log(f"  ! {last}")
                    break
                data = r.json()
                try:
                    cand = data["candidates"][0]
                    text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
                except (KeyError, IndexError):
                    last = f"{model}: empty answer {json.dumps(data)[:300]}"
                    self.log(f"  ! {last}")
                    break
                if cand.get("finishReason") not in (None, "STOP"):
                    self.log(f"  ! finishReason={cand.get('finishReason')}")
                try:
                    out = parse_json(text)
                    self.log(f"  model used: {model}")
                    return out
                except (LLMError, json.JSONDecodeError) as e:
                    last = f"{model}: bad JSON ({e})"
                    self.log(f"  ! {last}")
                    continue
        raise LLMError(f"Gemini failed on every model. Last error: {last}")


class Anthropic:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, models, log=print):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        self.models, self.log = models, log

    def json(self, system: str, user: str, schema: dict | None = None, temperature: float = 0.7) -> dict:
        if schema:
            user += "\n\n# OUTPUT\nReturn only one JSON object that matches this JSON Schema, with no text before or after it:\n" \
                    + json.dumps(schema, ensure_ascii=False)
        body = {"max_tokens": 24000, "temperature": temperature, "system": system,
                "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        last = None
        for model in self.models:
            for attempt in range(3):
                try:
                    r = requests.post(self.URL, json={**body, "model": model}, headers=headers, timeout=600)
                except requests.RequestException as e:
                    last = f"{model}: {e}"
                    time.sleep(_retry_wait(attempt))
                    continue
                if r.status_code in (429, 500, 502, 503, 529):
                    last = f"{model}: HTTP {r.status_code}"
                    self.log(f"  ! {last}")
                    time.sleep(_retry_wait(attempt))
                    continue
                if r.status_code != 200:
                    last = f"{model}: HTTP {r.status_code} {r.text[:300]}"
                    self.log(f"  ! {last}")
                    break
                text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
                try:
                    out = parse_json(text)
                    self.log(f"  model used: {model}")
                    return out
                except (LLMError, json.JSONDecodeError) as e:
                    last = f"{model}: bad JSON ({e})"
                    self.log(f"  ! {last}")
        raise LLMError(f"Claude failed on every model. Last error: {last}")


class Mock:
    """Offline provider for testing: returns fixture files."""

    def __init__(self, fixture_dir: Path, log=print):
        self.dir, self.log, self.calls = fixture_dir, log, 0

    def json(self, system: str, user: str, schema: dict | None = None, temperature: float = 0.7) -> dict:
        self.calls += 1
        is_picks = bool(schema) and "note" in schema.get("properties", {})
        name = "picks.json" if is_picks else "content.json"
        return json.loads((self.dir / name).read_text(encoding="utf-8"))


def make_client(provider: str, cfg: dict, root: Path, log=print):
    if provider == "gemini":
        return Gemini(cfg["models"]["gemini"], log)
    if provider == "anthropic":
        return Anthropic(cfg["models"]["anthropic"], log)
    if provider == "mock":
        return Mock(root / "tests" / "fixtures", log)
    raise LLMError(f"Unknown provider: {provider}")


# ---------- JSON schema for the issue content ----------

def _s(desc=None):
    return {"type": "string", **({"description": desc} if desc else {})}


def _arr(item, n=None, desc=None):
    a = {"type": "array", "items": item}
    if n:
        a["minItems"] = a["maxItems"] = n
    if desc:
        a["description"] = desc
    return a


def _obj(props: dict, desc=None):
    o = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    if desc:
        o["description"] = desc
    return o


def content_schema(opinion: str) -> dict:
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
    props.update({
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
                               "thai": _s("Thai translation"), "example": _s()}), 12),
    })
    return _obj(props)


PICKS_SCHEMA = _obj({"cover": _s(), "world": _arr(_s(), 2), "thailand": _arr(_s(), 2), "note": _s()})
