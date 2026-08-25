"""Layout-aware PDF extraction for bilingual paper generation."""

import re
from pathlib import Path

import fitz

QUESTION_RE = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})[\.\)]\s*")
HEADER_RE = re.compile(r"^\s*(English\s+हिंदी|I\s+PUC\s+JEE\s*\(MAINS\)|Page\s+\d+)\s*$", re.I)


def _clean(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _column_key(x0: float, page_width: float) -> int:
    ratio = x0 / max(page_width, 1)
    if ratio < 0.25:
        return 0
    if ratio < 0.50:
        return 1
    if ratio < 0.75:
        return 2
    return 3


def _is_question_only_number(text: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:Q\.?\s*)?\d{1,3}[\.\)]\s*", text or ""))


def _merge_question_number_blocks(blocks: list[dict]) -> list[dict]:
    """Join a standalone question number with the text immediately after it."""
    result: list[dict] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if _is_question_only_number(block.get("text", "")) and i + 1 < len(blocks):
            nxt = blocks[i + 1]
            b1, b2 = block["bbox"], nxt["bbox"]
            same_column = abs(b1[0] - b2[0]) < 30 or block.get("column") == nxt.get("column")
            close_vertically = 0 <= b2[1] - b1[3] < 18
            if same_column and close_vertically:
                merged = dict(nxt)
                merged["text"] = f"{block['text'].strip()} {nxt['text'].strip()}".strip()
                merged["bbox"] = [
                    min(b1[0], b2[0]), min(b1[1], b2[1]),
                    max(b1[2], b2[2]), max(b1[3], b2[3]),
                ]
                result.append(merged)
                i += 2
                continue
        result.append(block)
        i += 1
    return result


def extract_document(pdf_path: Path) -> dict:
    pdf = fitz.open(pdf_path)
    pages = []
    image_root = pdf_path.parent / f"{pdf_path.stem}_images"
    image_root.mkdir(exist_ok=True)

    for page_no, page in enumerate(pdf, start=1):
        data = page.get_text("dict")
        blocks = []

        for block_index, block in enumerate(data.get("blocks", [])):
            bbox = list(block.get("bbox", [0, 0, 0, 0]))
            x0, y0, x1, y1 = bbox

            # Keep useful diagrams/graphs. Small logos/header images are ignored.
            if block.get("type") == 1:
                width, height = x1 - x0, y1 - y0
                if y0 > 55 and width > 45 and height > 35:
                    image_bytes = block.get("image")
                    if image_bytes:
                        ext = block.get("ext", "png")
                        image_path = image_root / f"page_{page_no}_image_{block_index}.{ext}"
                        image_path.write_bytes(image_bytes)
                        blocks.append({
                            "page": page_no,
                            "type": "image",
                            "text": "",
                            "english": "",
                            "hindi": "",
                            "bbox": bbox,
                            "column": _column_key(x0, page.rect.width),
                            "image_path": str(image_path),
                            "width": width,
                            "height": height,
                        })
                continue

            if block.get("type") != 0:
                continue

            lines = []
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                txt = "".join(span.get("text", "") for span in spans)
                if txt.strip():
                    lines.append(txt.strip())

            text = _clean("\n".join(lines))
            if not text or HEADER_RE.fullmatch(text):
                continue

            blocks.append({
                "page": page_no,
                "type": "text",
                "text": text,
                "bbox": bbox,
                "column": _column_key(x0, page.rect.width),
                "font_size": max(
                    [s.get("size", 0) for line in block.get("lines", []) for s in line.get("spans", [])] or [0]
                ),
            })

        blocks.sort(key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
        blocks = _merge_question_number_blocks(blocks)
        pages.append({"page": page_no, "width": page.rect.width, "height": page.rect.height, "blocks": blocks})

    return {"pages": pages}
