"""Layout-aware PDF extraction foundation.

We keep coordinates instead of joining the PDF into one plain-text string.
This is important for detecting 2/4-column options and preserving spatial order.
"""

import fitz
import re
from pathlib import Path

QUESTION_RE = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})[\.\)]\s+")


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _column_key(x0: float, page_width: float) -> int:
    # Four coarse horizontal zones. Later versions will use actual clustering.
    ratio = x0 / max(page_width, 1)
    if ratio < 0.25:
        return 0
    if ratio < 0.50:
        return 1
    if ratio < 0.75:
        return 2
    return 3


def extract_document(pdf_path: Path) -> dict:
    pdf = fitz.open(pdf_path)
    pages = []

    for page_no, page in enumerate(pdf, start=1):
        data = page.get_text("dict")
        blocks = []

        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue

            lines = []
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                txt = "".join(span.get("text", "") for span in spans)
                if txt.strip():
                    lines.append(txt)

            text = _clean("\n".join(lines))
            if not text:
                continue

            x0, y0, x1, y1 = block["bbox"]
            blocks.append({
                "page": page_no,
                "text": text,
                "bbox": [x0, y0, x1, y1],
                "column": _column_key(x0, page.rect.width),
                "font_size": max(
                    [s.get("size", 0) for line in block.get("lines", []) for s in line.get("spans", [])] or [0]
                ),
            })

        # Reading order: top-to-bottom, then left-to-right.
        blocks.sort(key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
        pages.append({"page": page_no, "width": page.rect.width, "height": page.rect.height, "blocks": blocks})

    return {"pages": pages}
