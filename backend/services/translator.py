"""English -> NCERT-style Hindi translation with exact math preservation."""

import json
import os
import re
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
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _question_number(text: str) -> str:
    match = QUESTION_RE.match(text or "")
    return match.group(1) if match else ""


def _remove_question_number(text: str) -> str:
    return QUESTION_RE.sub("", text or "", count=1).strip()


def _has_hindi(text: str) -> bool:
    return any("\u0900" <= c <= "\u097F" for c in str(text or ""))


def _is_math_only(text: str) -> bool:
    return not MATH_TOKEN_RE.sub("", text or "").strip()


class DemoTranslator:
    """Gemini translator used by the production paper-generation pipeline."""

    def __init__(self, api_key: str = ""):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip()
        self.client = genai.Client(api_key=self.api_key) if self.api_key and genai else None

    def _translate_batch(self, items: list[dict[str, str]], retry: bool = False) -> dict[str, str]:
        if not items:
            return {}
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        strict = "" if not retry else "\nIMPORTANT: Previous output was incomplete. Every item MUST contain a non-empty Hindi translation containing Devanagari characters. Return every ID exactly once."
        prompt = f"""Translate the following competitive-exam paper text from English to clear, formal NCERT-style Hindi.{strict}
Return ONLY a JSON array:
[{{"id":"same-id","hindi":"translation"}}]

Rules:
1. Translate every natural-language English word faithfully. Do not solve, explain, shorten, or add content.
2. Preserve complete meaning and original wording.
3. Every __MATH_n__ token is an exact mathematical fragment from the source. Preserve every token exactly,
   in the same position, with the same spelling. Never translate, remove, merge, reorder, or invent math tokens.
4. Preserve numbers, units, symbols, variables, option markers, set notation, scientific symbols and formula tokens.
5. Use standard NCERT Hindi terminology. Keep unavoidable technical terms in English only when appropriate.
6. Preserve source line breaks (\\n) in the same positions.
7. Hindi must be real Devanagari Hindi, not transliterated English.
8. Return one object for EVERY input ID.

INPUT:
{json.dumps(items, ensure_ascii=False)}"""

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        try:
            parsed = json.loads(_strip_json_fence(response.text or "[]"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Gemini returned invalid translation JSON: {exc}") from exc

        result: dict[str, str] = {}
        for item in parsed if isinstance(parsed, list) else []:
            if not isinstance(item, dict) or "id" not in item:
                continue
            value = str(item.get("hindi", "")).strip()
            if value and (_has_hindi(value) or _is_math_only(value)):
                result[str(item["id"])] = value

        missing = [item["id"] for item in items if not result.get(item["id"])]
        if missing and not retry:
            return self._translate_batch([item for item in items if item["id"] in missing], retry=True) | {k: v for k, v in result.items() if k not in missing}
        if missing:
            raise RuntimeError("Gemini did not return usable Hindi for: " + ", ".join(missing))
        return result

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
                if _is_math_only(clean_source):
                    block["hindi"] = clean_source
                    continue
                item_counter_id = block.get("_translation_id")
                if item_counter_id:
                    queue.append({"id": item_counter_id, "text": clean_source})

            translations: dict[str, str] = {}
            for start in range(0, len(queue), 8):
                translations.update(self._translate_batch(queue[start:start + 8]))

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

                # Keep instructions/section descriptions only after the paper has started.
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
                current["blocks"].append(copied)

        flush_question()

        # Keep the real question order. Do not silently drop valid questions if
        # a PDF page contains a layout/header block between question numbers.
        questions.sort(key=lambda q: int(q.get("number", 0)))
        return {"questions": questions, "source": document}
