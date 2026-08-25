"""English -> NCERT-style Hindi translation layer.

Uses Gemini when GEMINI_API_KEY is configured. The source text is sent in
numbered blocks so question numbering, options, formulas and symbols can be
preserved while only natural-language English is translated.
"""

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
FORMULA_RE = re.compile(r"(?:[A-Za-z]\w*\s*=\s*[^,.;\n]+|[A-Za-z]+\d+(?:[A-Za-z0-9+\-]*)|[√∑∫∞≤≥≠≈∝±×÷→⇌πθλμρΩΔ]|\b\d+(?:\.\d+)?(?:\s*[×x/]\s*10\s*\^?\s*[−-]?\d+)?\b)")


def _protect_formulas(text: str):
    formulas = []
    def repl(match):
        key = f"__FORMULA_{len(formulas)}__"
        formulas.append(match.group(0))
        return key
    return FORMULA_RE.sub(repl, text), formulas


def _restore_formulas(text: str, formulas):
    for i, formula in enumerate(formulas):
        text = text.replace(f"__FORMULA_{i}__", formula)
    return text


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class DemoTranslator:
    """Gemini translator with a safe fallback when no API key is configured."""

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
        self.client = genai.Client(api_key=self.api_key) if self.api_key and genai else None

    def translate_text(self, text: str) -> str:
        if not text:
            return ""
        if not self.client:
            return "[Hindi translation pending]"
        protected, formulas = _protect_formulas(text)
        prompt = (
            "Translate this exam-paper text into NCERT-style Hindi. Keep formulas, numbers, "
            "units, symbols, variables, option letters and placeholders exactly unchanged. "
            "Translate only natural-language English. Do not explain or add anything.\n\nSOURCE:\n" + protected
        )
        response = self.client.models.generate_content(model=self.model, contents=prompt, config=types.GenerateContentConfig())
        return _restore_formulas((response.text or "").strip(), formulas)

    def _translate_batch(self, items: list[dict[str, str]]) -> dict[str, str]:
        if not items:
            return {}
        if not self.client:
            return {item["id"]: "[Hindi translation pending]" for item in items}

        protected_items = []
        formula_maps: dict[str, list[str]] = {}
        for item in items:
            protected, formulas = _protect_formulas(item["text"])
            formula_maps[item["id"]] = formulas
            protected_items.append({"id": item["id"], "text": protected})

        prompt = """You are translating a competitive-exam paper from English to NCERT-style Hindi.
Return ONLY a JSON array. For every input object, return exactly one object with the same id and a Hindi translation.
Rules:
1. Preserve the exact meaning and wording; do not shorten or solve anything.
2. Translate natural-language English into clear NCERT-style Hindi.
3. Preserve every formula placeholder, number, unit, symbol, variable, option marker, and mathematical expression exactly.
4. Do not translate placeholders such as __FORMULA_0__.
5. Do not add explanations, answers, or commentary.
6. Use standard NCERT Hindi terminology where established; otherwise retain technical English terms.

INPUT:
""" + json.dumps(protected_items, ensure_ascii=False)

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        try:
            parsed = json.loads(_strip_json_fence(response.text or "[]"))
        except json.JSONDecodeError:
            return {item["id"]: "[Hindi translation failed]" for item in items}

        result: dict[str, str] = {}
        for item in parsed if isinstance(parsed, list) else []:
            if isinstance(item, dict) and "id" in item:
                item_id = str(item["id"])
                result[item_id] = _restore_formulas(str(item.get("hindi", "")), formula_maps.get(item_id, []))
        for item in items:
            result.setdefault(item["id"], "[Hindi translation failed]")
        return result

    def translate_document(self, document: dict) -> dict:
        questions = []
        current: dict[str, Any] | None = None
        pending_items: list[dict[str, str]] = []
        item_counter = 0

        def flush_question():
            nonlocal current, pending_items
            if not current:
                return
            translations = self._translate_batch(pending_items)
            for block in current["blocks"]:
                if block.get("type") == "image":
                    continue
                block["hindi"] = translations.get(block["_translation_id"], "[Hindi translation failed]")
                block.pop("_translation_id", None)
            questions.append(current)
            current = None
            pending_items = []

        for page in document["pages"]:
            for block in page["blocks"]:
                text = block.get("text", "")
                q = QUESTION_RE.match(text) if block.get("type") != "image" else None
                if q:
                    flush_question()
                    current = {"number": int(q.group(1)), "blocks": []}
                if current is None:
                    continue

                copied = dict(block)
                if block.get("type") == "image":
                    copied["english"] = ""
                    copied["hindi"] = ""
                else:
                    item_counter += 1
                    translation_id = f"b{item_counter}"
                    copied["english"] = text
                    copied["hindi"] = ""
                    copied["_translation_id"] = translation_id
                    pending_items.append({"id": translation_id, "text": text})
                current["blocks"].append(copied)

        flush_question()
        return {"questions": questions, "source": document}
