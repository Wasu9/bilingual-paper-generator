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

def _extract_json_value(text: str) -> Any:
    """Best-effort JSON extraction without relying on the SDK JSON decoder."""
    raw = _strip_json_fence(text)
    try:
        return json.loads(raw)
    except Exception:
        pass
    starts = [i for i in (raw.find("["), raw.find("{")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    for end in range(len(raw), start, -1):
        candidate = raw[start:end].strip()
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None

class DemoTranslator:
    def __init__(self, api_key: str = ""):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip()
        self.fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash").strip()
        self.client = genai.Client(api_key=self.api_key) if self.api_key and genai else None

    def _call_model(self, model: str, prompt: str, json_mode: bool = False):
        last_error = None
        for attempt in range(3):
            try:
                # Use plain text transport even for prompts that request JSON.
                # This prevents malformed/truncated structured output from being
                # parsed by the SDK before our own recovery code can run.
                config = types.GenerateContentConfig(response_mime_type="text/plain")
                return self.client.models.generate_content(model=model, contents=prompt, config=config)
            except Exception as exc:
                last_error = exc
                if not _is_transient_error(exc) or attempt == 2:
                    break
                time.sleep(2 ** attempt)
        raise last_error or RuntimeError("Gemini request failed")

    def _parse_response(self, response_text: str) -> dict[str, str]:
        raw = _strip_json_fence(response_text)
        parsed = _extract_json_value(raw)
        if parsed is None:
            return {"__index_0": raw} if _has_hindi(raw) else {}
        if isinstance(parsed, dict):
            parsed = [parsed]
        result = {}
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
        prompt = f'''Translate the following competitive-exam paper text from English to clear, formal NCERT-style Hindi.
Return ONLY a JSON array with one object for EVERY input item:
[{{"id":"same-id","hindi":"translation"}}]
Rules:
1. Translate every natural-language English word faithfully. Do not solve, explain, shorten, or add content.
2. Preserve complete meaning and original wording.
3. Preserve every __MATH_n__ token exactly, in the same position.
4. Preserve numbers, units, symbols, variables, option markers, set notation and scientific symbols.
5. Use standard NCERT Hindi terminology.
6. Preserve source line breaks.
7. Hindi must contain real Devanagari characters.
8. Never omit an input item.
INPUT:
{json.dumps(items, ensure_ascii=False)}'''
        last_error = None
        for model in self._models():
            try:
                response = self._call_model(model, prompt, json_mode=False)
                parsed = self._parse_response(response.text or "")
                result = {k: v for k, v in parsed.items() if not k.startswith("__index_")}
                indexed = {k: v for k, v in parsed.items() if k.startswith("__index_")}
                missing = [item for item in items if not result.get(item["id"])]
                # If the model returned a valid ordered array but dropped ids,
                # recover those entries by their original batch position.
                for index, item in enumerate(items):
                    if not result.get(item["id"]) and indexed.get(f"__index_{index}"):
                        result[item["id"]] = indexed[f"__index_{index}"]
                missing = [item for item in items if not result.get(item["id"])]
                # Recover missing entries individually instead of failing the whole paper.
                for item in missing:
                    value = self._translate_single(item, model)
                    if value:
                        result[item["id"]] = value
                still_missing = [item["id"] for item in items if not result.get(item["id"])]
                if not still_missing:
                    return result
                raise RuntimeError("Gemini did not return usable Hindi for: " + ", ".join(still_missing))
            except Exception as exc:
                last_error = exc
                if not _is_transient_error(exc):
                    raise
        raise RuntimeError(f"Gemini translation service is temporarily unavailable: {last_error}") from last_error

    def _translate_single(self, item: dict[str, str], preferred_model: str | None = None) -> str:
        source = item.get("text", "").strip()
        if _is_math_only(source) or _is_nonlinguistic_fragment(source):
            return source
        plain_prompt = f'''Translate ONLY the following text into formal NCERT-style Hindi.
Do not explain, solve, summarize, or add anything.
Preserve every __MATH_n__ token exactly, including its spelling, order and position.
Preserve numbers, units, symbols, variables and line breaks.
Use real Devanagari Hindi. Return ONLY the Hindi translation.
TEXT:
{source}'''
        models = [preferred_model] if preferred_model else []
        models.extend(m for m in self._models() if m not in models)
        for model in models:
            try:
                response = self._call_model(model, plain_prompt, json_mode=False)
                value = _strip_json_fence(response.text or "")
                if _has_hindi(value):
                    return value
            except Exception:
                pass
            try:
                response = self._call_model(model, f'''Return ONLY JSON: {{"hindi":"..."}}\nTranslate to NCERT-style Hindi. Preserve __MATH_n__ tokens exactly.\nTEXT: {source}''', json_mode=False)
                parsed = self._parse_response(response.text or "{}")
                if parsed.get("__index_0"):
                    return parsed["__index_0"]
            except Exception:
                pass
        return ""

    def _merge_question_block_text(self, blocks: list[dict]) -> list[dict]:
        """Merge fragmented PDF text blocks belonging to the same question line."""
        out: list[dict] = []
        for block in blocks:
            if not out or block.get("type") != "text" or out[-1].get("type") != "text":
                out.append(block)
                continue
            prev = out[-1]
            pb = prev.get("bbox", [0,0,0,0]); cb = block.get("bbox", [0,0,0,0])
            same_page = prev.get("page") == block.get("page")
            same_col = prev.get("column") == block.get("column")
            vertical_gap = float(cb[1]) - float(pb[3])
            overlaps_y = not (float(cb[1]) > float(pb[3]) + 14 or float(pb[1]) > float(cb[3]) + 14)
            starts_q = bool(QUESTION_RE.match(str(block.get("text", ""))))
            prev_is_complete = str(prev.get("text", "")).rstrip().endswith((".", ":", "?"))
            if same_page and same_col and not starts_q and vertical_gap <= 8 and (overlaps_y or not prev_is_complete):
                prev_text = str(prev.get("text", "")).strip()
                cur_text = str(block.get("text", "")).strip()
                prev_source = str(prev.get("translation_source") or prev_text).strip()
                cur_source = str(block.get("translation_source") or cur_text).strip()
                prev["text"] = (prev_text + " " + cur_text).strip()
                prev["translation_source"] = (prev_source + " " + cur_source).strip()
                prev["lines"] = list(prev.get("lines", [])) + list(block.get("lines", []))
                prev["math_values"] = list(prev.get("math_values", [])) + list(block.get("math_values", []))
                prev["bbox"] = [min(pb[0],cb[0]), min(pb[1],cb[1]), max(pb[2],cb[2]), max(pb[3],cb[3])]
                continue
            out.append(block)
        return out

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
            current["blocks"] = self._merge_question_block_text(current["blocks"])
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
            translations = {}
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
                    continue
                copied = dict(block)
                copied["english"] = text
                copied["hindi"] = ""
                item_counter += 1
                copied["_translation_id"] = f"b{item_counter}"
                current["blocks"].append(copied)
        if current:
            flush_question()
        questions.sort(key=lambda q: int(q.get("number", 0)))
        return {"questions": questions, "source": document}
