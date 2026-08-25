"""English -> NCERT-style Hindi translation with exact math placeholders."""

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
    m = QUESTION_RE.match(text or "")
    return m.group(1) if m else ""


def _remove_question_number(text: str) -> str:
    return QUESTION_RE.sub("", text or "", count=1).strip()


def _is_math_only(text: str) -> bool:
    if not text:
        return True
    return bool(MATH_TOKEN_RE.sub("", text).strip() == "")


class DemoTranslator:
    """Gemini translator used by the paper-generation pipeline."""

    def __init__(self, api_key: str = ""):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip()
        self.client = genai.Client(api_key=self.api_key) if self.api_key and genai else None

    def _translate_batch(self, items: list[dict[str, str]]) -> dict[str, str]:
        if not items:
            return {}
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        prompt = """Translate the following competitive-exam paper text from English to clear NCERT-style Hindi.
Return ONLY a JSON array:
[{"id":"same-id","hindi":"translation"}]

Rules:
1. Translate every natural-language English word faithfully; do not shorten, solve, explain, or add.
2. Preserve the complete meaning and question wording.
3. Every __MATH_n__ token is an exact mathematical fragment from the source. Preserve every token exactly,
   in the same position, with the same spelling. Never translate, remove, merge, reorder, or invent math tokens.
4. Preserve numbers, units, symbols, variables, option markers, set notation, and scientific terminology.
5. Use standard NCERT Hindi terminology for established terms; retain technical English only where a standard Hindi term is unclear.
6. Preserve every source line break (\n) in the same position; do not merge separate lines.
7. Never return an empty Hindi field.

INPUT:
""" + json.dumps(items, ensure_ascii=False)

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        try:
            parsed = json.loads(_strip_json_fence(response.text or "[]"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Gemini returned invalid translation JSON: {exc}") from exc

        result: dict[str, str] = {}
        for item in parsed if isinstance(parsed, list) else []:
            if isinstance(item, dict) and "id" in item:
                value = str(item.get("hindi", "")).strip()
                if value:
                    result[str(item["id"])] = value

        missing = [item["id"] for item in items if not result.get(item["id"])]
        if missing:
            raise RuntimeError("Gemini did not return translations for: " + ", ".join(missing))
        return result

    def translate_document(self, document: dict) -> dict:
        questions: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        pending_section: str | None = None
        exam_started = False
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
                if not source:
                    continue
                clean_source = _remove_question_number(source)
                if _is_math_only(clean_source):
                    block["hindi"] = clean_source
                    continue
                item_counter_id = block.get("_translation_id")
                queue.append({"id": item_counter_id, "text": clean_source})

            translations: dict[str, str] = {}
            for start in range(0, len(queue), 10):
                translations.update(self._translate_batch(queue[start:start + 10]))

            for block in current["blocks"]:
                if block.get("type") == "image":
                    block["hindi"] = ""
                    continue
                translation_id = block.get("_translation_id")
                translated = translations.get(translation_id, block.get("hindi", ""))
                if not translated:
                    raise RuntimeError(f"Empty Hindi translation for question {current['number']}")
                number = _question_number(block.get("english", ""))
                if number and not QUESTION_RE.match(translated):
                    translated = f"{number}. {translated}"
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

                if not exam_started:
                    if "marking scheme" in text.lower() or "single correct choice type" in text.lower() or "numerical value answer type" in text.lower():
                        exam_started = True
                    else:
                        continue

                q = QUESTION_RE.match(text) if block.get("type") == "text" else None
                if q:
                    flush_question()
                    current = {"number": int(q.group(1)), "section": pending_section, "blocks": []}
                    pending_section = None

                if current is None:
                    continue

                copied = dict(block)
                if block.get("type") == "image":
                    copied["english"] = ""
                    copied["hindi"] = ""
                    current["blocks"].append(copied)
                    continue

                item_counter += 1
                copied["english"] = text
                copied["hindi"] = ""
                copied["_translation_id"] = f"b{item_counter}"
                current["blocks"].append(copied)

        flush_question()

        filtered = []
        expected = 1 if questions else None
        for idx, question in enumerate(questions):
            if expected is None:
                break
            number = int(question.get("number", -1))
            if number == expected:
                filtered.append(question)
                expected += 1
                continue
            if number < expected:
                continue
            future_numbers = {int(q.get("number", -1)) for q in questions[idx + 1:]}
            if expected in future_numbers:
                continue
            filtered.append(question)
            expected = number + 1

        return {"questions": filtered, "source": document}
