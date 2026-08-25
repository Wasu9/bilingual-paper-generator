"""English -> NCERT-style Hindi translation layer."""

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
FORMULA_RE = re.compile(
    r"(?:[A-Za-z]\w*\s*=\s*[^,.;\n]+|[A-Za-z]+\d+(?:[A-Za-z0-9+\-]*)|"
    r"[√∑∫∞≤≥≠≈∝±×÷→⇌πθλμρΩΔ]|\b\d+(?:\.\d+)?(?:\s*[×x/]\s*10\s*\^?\s*[−-]?\d+)?\b)"
)


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


def _question_number(text: str) -> str:
    match = QUESTION_RE.match(text or "")
    return match.group(1) if match else ""


def _remove_question_number(text: str) -> str:
    return QUESTION_RE.sub("", text or "", count=1).strip()


class DemoTranslator:
    """Gemini translator used by the paper-generation pipeline."""

    def __init__(self, api_key: str = ""):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip()
        self.client = genai.Client(api_key=self.api_key) if self.api_key and genai else None

    def translate_text(self, text: str) -> str:
        if not text:
            return ""
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        protected, formulas = _protect_formulas(text)
        prompt = (
            "Translate this competitive-exam paper text into NCERT-style Hindi. "
            "Translate only natural-language English. Preserve formulas, numbers, units, "
            "symbols, variables and mathematical expressions exactly. Do not solve, explain, "
            "shorten or add anything. Return only the Hindi translation.\n\nSOURCE:\n" + protected
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0),
        )
        return _restore_formulas((response.text or "").strip(), formulas)

    def _translate_batch(self, items: list[dict[str, str]]) -> dict[str, str]:
        if not items:
            return {}
        if not self.client:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        protected_items = []
        formula_maps: dict[str, list[str]] = {}
        for item in items:
            protected, formulas = _protect_formulas(item["text"])
            formula_maps[item["id"]] = formulas
            protected_items.append({"id": item["id"], "text": protected})

        prompt = """Translate the following competitive-exam paper text from English to NCERT-style Hindi.
Return ONLY a JSON array of objects in exactly this form:
[{"id":"same-id","hindi":"translation"}]

Rules:
1. Preserve the complete meaning and wording. Do not shorten or solve.
2. Translate natural-language English into clear NCERT-style Hindi.
3. Preserve every __FORMULA_n__ placeholder exactly; never translate or remove it.
4. Preserve numbers, units, symbols, variables, mathematical expressions and option markers.
5. Do not add explanations, answers, notes or commentary.
6. Keep established NCERT technical terminology in Hindi; where there is no clear standard term, retain the technical English term.
7. Never return an empty Hindi field.

INPUT:
""" + json.dumps(protected_items, ensure_ascii=False)

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
                item_id = str(item["id"])
                result[item_id] = _restore_formulas(
                    str(item.get("hindi", "")).strip(), formula_maps.get(item_id, [])
                )

        missing = [item["id"] for item in items if not result.get(item["id"])]
        if missing:
            raise RuntimeError("Gemini did not return translations for: " + ", ".join(missing))
        return result

    def translate_document(self, document: dict) -> dict:
        questions: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        pending_images: list[dict] = []

        def flush_question():
            nonlocal current
            if not current:
                return

            queue = [
                {"id": block["_translation_id"], "text": _remove_question_number(block["english"])}
                for block in current["blocks"]
                if block.get("type") == "text" and block.get("_translation_id")
            ]
            translations: dict[str, str] = {}
            for start in range(0, len(queue), 12):
                translations.update(self._translate_batch(queue[start:start + 12]))

            for block in current["blocks"]:
                if block.get("type") == "image":
                    continue
                translation_id = block.pop("_translation_id", None)
                translated = translations.get(translation_id, "")
                if not translated:
                    raise RuntimeError(f"Empty Hindi translation for question {current['number']}")
                number = _question_number(block.get("english", ""))
                if number and not QUESTION_RE.match(translated):
                    translated = f"{number}. {translated}"
                block["hindi"] = translated

            questions.append(current)
            current = None

        item_counter = 0
        for page in document["pages"]:
            for block in page["blocks"]:
                text = block.get("text", "")
                q = QUESTION_RE.match(text) if block.get("type") != "image" else None

                if q:
                    flush_question()
                    current = {"number": int(q.group(1)), "blocks": []}
                    if pending_images:
                        current["blocks"].extend(pending_images)
                        pending_images = []

                if current is None:
                    if block.get("type") == "image":
                        pending_images.append(dict(block))
                    continue

                copied = dict(block)
                if block.get("type") == "image":
                    copied["english"] = ""
                    copied["hindi"] = ""
                    current["blocks"].append(copied)
                    continue

                item_counter += 1
                translation_id = f"b{item_counter}"
                copied["english"] = text
                copied["hindi"] = ""
                copied["_translation_id"] = translation_id
                current["blocks"].append(copied)

        flush_question()
        return {"questions": questions, "source": document}
