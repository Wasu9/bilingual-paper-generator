"""English -> NCERT-style Hindi translation with exact math preservation."""

import json
import os
import re
import time
from typing import Any

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

QUESTION_RE = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})[\.\)]\s*")
MATH_TOKEN_RE = re.compile(r"__MATH_\d+__")
SECTION_RE = re.compile(r"^\s*(PHYSICS|CHEMISTRY|MATHEMATICS|BIOLOGY|BOTANY|ZOOLOGY)\s*$", re.I)


def _strip_json_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _remove_question_number(text: str) -> str:
    return QUESTION_RE.sub("", text or "", count=1).strip()


def _has_hindi(text: str) -> bool:
    return any("\u0900" <= c <= "\u097F" for c in str(text or ""))


def _is_math_only(text: str) -> bool:
    return not MATH_TOKEN_RE.sub("", text or "").strip()


def _is_nonlinguistic_fragment(text: str) -> bool:
    value = MATH_TOKEN_RE.sub("", str(text or "")).strip()
    if not value:
        return True
    return bool(re.fullmatch(r"(?:\(?[A-Da-d]\)?|[0-9]+|[ivxIVX]+|[\W_]+)", value))


def _is_transient_error(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in {408, 429, 500, 502, 503, 504}:
        return True
    text = str(exc).upper()
    return any(token in text for token in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "502", "504"))


class DemoTranslator:
    def __init__(self, api_key: str = ""):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        # Current stable production models. Gemini 3.7 Flash is the newest
        # stable Flash model; 3.6/3.5 remain valid fallbacks.
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip()
        self.fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash").strip()
        self.client = genai.Client(api_key=self.api_key) if self.api_key and genai else None

    def _call_model(self, model: str, prompt: str, json_mode: bool = True):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                config = types.GenerateContentConfig(
                    response_mime_type="application/json" if json_mode else "text/plain"
                )
                return self.client.models.generate_content(model=model, contents=prompt, config=config)
            except Exception as exc:
                last_error = exc
                if not _is_transient_error(exc) or attempt == 2:
                    break
                time.sleep(2 ** attempt)
        raise last_error or RuntimeError("Gemini request failed")

    def _parse_response(self, response_text: str) -> dict[str, str]:
        raw = _strip_json_fence(response_text)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Some valid Gemini responses contain Hindi but don't obey the JSON
            # envelope. For recovery requests, keep the Hindi text itself.
            if _has_hindi(raw):
                return {"__index_0": raw}
            return {}

        if isinstance(parsed, dict):
            parsed = [parsed]
        result: dict[str, str] = {}
        for index, item in enumerate(parsed if isinstance(parsed, list) else []):
            if not isinstance(item, dict):
                continue
            value = str(item.get("hindi", "")).strip()
            if not value or not (_has_hindi(value) or _is_math_only(value)):
                continue
            key = str(item.get("id", ""))
            if key:
                result[key] = value
            result[f"__index_{index}"] = value
        return result

    def _models(self):
        models = [self.model]
        if self.fallback_model and self.fallback_model not in models:
            models.append(self.fallback_model)
        return models

    def _translate_batch(self, items: list[dict[str, str]]) -> dict[str, str]:
        if not items:
            return {}
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        prompt = f"""Translate the following competitive-exam paper text from English to clear, formal NCERT-style Hindi.
Return ONLY a JSON array with one object for EVERY input item:
[{{"id":"same-id","hindi":"translation"}}]

Rules:
1. Translate every natural-language English word faithfully. Do not solve, explain, shorten, or add content.
2. Preserve complete meaning and original wording.
3. Every __MATH_n__ token is an exact mathematical fragment from the source. Preserve every token exactly, in the same position.
4. Preserve numbers, units, symbols, variables, option markers, set notation and scientific symbols.
5. Use standard NCERT Hindi terminology.
6. Preserve source line breaks (\\n).
7. Hindi must contain real Devanagari characters.
8. Never omit an input item, even if it is short.

INPUT:
{json.dumps(items, ensure_ascii=False)}"""

        last_error: Exception | None = None
        for model in self._models():
            try:
                response = self._call_model(model, prompt, json_mode=True)
                parsed = self._parse_response(response.text or "[]")
                result = {k: v for k, v in parsed.items() if not k.startswith("__index_")}
                missing = [item for item in items if not result.get(item["id"])]
                if not missing:
                    return result
                for item in missing:
                    value = self._translate_single(item, model)
                    if value:
                        result[item["id"]] = value
                still_missing = [item["id"] for item in items if not result.get(item["id"])]
                if still_missing:
                    raise RuntimeError("Gemini did not return usable Hindi for: " + ", ".join(still_missing))
                return result
            except Exception as exc:
                last_error = exc
                if not _is_transient_error(exc):
                    raise
        raise RuntimeError(f"Gemini translation service is temporarily unavailable: {last_error}") from last_error

    def _translate_single(self, item: dict[str, str], preferred_model: str | None = None) -> str:
        source = item.get("text", "").strip()
        if _is_math_only(source) or _is_nonlinguistic_fragment(source):
            return source

        # First ask for plain Hindi rather than JSON. This avoids the common
        # failure where a short block is omitted by structured-output decoding.
        plain_prompt = f"""Translate ONLY the following text into formal NCERT-style Hindi.
Do not explain, solve, summarize, or add anything.
Preserve every __MATH_n__ token exactly, including its spelling, order and position.
Preserve numbers, units, symbols, variables and line breaks.
Use real Devanagari Hindi. Return ONLY the Hindi translation.

TEXT:
{source}"""

        models = [preferred_model] if preferred_model else []
        models.extend(m for m in self._models() if m not in models)
        last_error: Exception | None = None
        for model in models:
            try:
                response = self._call_model(model, plain_prompt, json_mode=False)
                value = _strip_json_fence(response.text or "")
                if _has_hindi(value):
                    return value
            except Exception as exc:
                last_error = exc
                if not _is_transient_error(exc):
                    continue

            # Second attempt for this same item with a tiny JSON contract.
            try:
                response = self._call_model(
                    model,
                    f'''Return ONLY JSON: {{"hindi":"..."}}\nTranslate to NCERT-style Hindi. Preserve __MATH_n__ tokens exactly.\nTEXT: {source}''',
                    json_mode=True,
                )
                parsed = self._parse_response(response.text or "{}")
                if parsed.get("__index_0"):
                    return parsed["__index_0"]
            except Exception as exc:
                last_error = exc
                continue
        return ""

    def translate_document(self, document: dict) -> dict:
        questions: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        pending_section: str | None = None
        started = False
        item_counter = 0

        def flush_question():
            nonlocal current
            if not current:
                return
            queue = []
            for block in current["blocks"]:
                if block.get("type") != "text":
                    continue
                source = block.get("translation_source") or block.get("text", "")
                clean_source = _remove_question_number(source)
                if not clean_source:
                    continue
                if _is_math_only(clean_source) or _is_nonlinguistic_fragment(clean_source):
                    block["hindi"] = clean_source
                    continue
                translation_id = block.get("_translation_id")
                if translation_id:
                    queue.append({"id": translation_id, "text": clean_source})

            translations: dict[str, str] = {}
            for start in range(0, len(queue), 4):
                translations.update(self._translate_batch(queue[start:start + 4]))

            for block in current["blocks"]:
                if block.get("type") == "image":
                    block["hindi"] = ""
                    continue
                translation_id = block.get("_translation_id")
                if translation_id:
                    translated = translations.get(translation_id, "").strip()
                    if not translated:
                        raise RuntimeError(f"Empty Hindi translation for question {current['number']}")
                    block["hindi"] = translated
                block.pop("_translation_id", None)
            questions.append(current)
            current = None

        for page in document.get("pages", []):
            for block in page.get("blocks", []):
                text = str(block.get("text", "") or "").strip()
                if block.get("type") == "image":
                    if current is not None:
                        current["blocks"].append(dict(block))
                    continue
                if not text:
                    continue
                if SECTION_RE.fullmatch(text):
                    flush_question()
                    pending_section = text.upper()
                    continue
                qmatch = QUESTION_RE.match(text)
                if qmatch:
                    flush_question()
                    current = {"number": int(qmatch.group(1)), "section": pending_section, "blocks": []}
                    pending_section = None
                    started = True
                if current is None:
                    if "marking scheme" in text.lower() or "single correct choice type" in text.lower() or "numerical value answer type" in text.lower():
                        started = True
                    continue
                if not started:
                    continue
                copied = dict(block)
                copied["english"] = text
                copied["hindi"] = ""
                item_counter += 1
                copied["_translation_id"] = f"b{item_counter}"
                current["blocks"].append(copied]

        flush_question()
        questions.sort(key=lambda q: int(q.get("number", 0)))
        return {"questions": questions, "source": document}
