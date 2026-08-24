"""Translation layer.

The production implementation should replace DemoTranslator with an AI provider
adapter. Formula-like fragments are protected before translation so symbols,
numbers, subscripts and equations are not translated accidentally.
"""

import re

QUESTION_RE = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})[\.\)]\s+")
OPTION_RE = re.compile(r"^\s*[\(\[]?([A-Da-d])[\)\].:-]\s*")
FORMULA_RE = re.compile(
    r"(?<!\w)(?:"
    r"[A-Za-z]\w*\s*=\s*[^,.;\n]+"
    r"|[A-Za-z]+\d+(?:[A-Za-z0-9+\-]*)"
    r"|[√∑∫∞≤≥≠≈∝±×÷→⇌πθλμρΩΔ]"
    r"|[A-Za-z]\^\{[^}]+\}"
    r")(?!\w)"
)


class DemoTranslator:
    """Safe demo mode: keeps English text and adds a visible placeholder.

    This is intentional: no fake AI translation is presented as NCERT translation.
    """

    def protect_formulas(self, text: str):
        formulas = []

        def repl(match):
            idx = len(formulas)
            formulas.append(match.group(0))
            return f"⟦FORMULA_{idx}⟧"

        return FORMULA_RE.sub(repl, text), formulas

    def restore(self, text, formulas):
        for i, formula in enumerate(formulas):
            text = text.replace(f"⟦FORMULA_{i}⟧", formula)
        return text

    def translate_text(self, text: str) -> str:
        protected, formulas = self.protect_formulas(text)
        # Deliberately not pretending this is an AI/NCERT translation.
        result = "[Hindi translation pending] " + protected
        return self.restore(result, formulas)

    def translate_document(self, document: dict) -> dict:
        questions = []
        current = None

        for page in document["pages"]:
            for block in page["blocks"]:
                text = block["text"]
                q = QUESTION_RE.match(text)

                if q:
                    if current:
                        questions.append(current)
                    current = {
                        "number": int(q.group(1)),
                        "blocks": [],
                    }

                if current is None:
                    continue

                current["blocks"].append({
                    **block,
                    "english": text,
                    "hindi": self.translate_text(text),
                })

        if current:
            questions.append(current)

        return {"questions": questions, "source": document}
