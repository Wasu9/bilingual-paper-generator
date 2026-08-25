"""Layout-aware PDF extraction for bilingual paper generation.

The parser keeps question blocks intact, preserves source figures, and keeps
math spans/positions so the DOCX generator can render them as Word math.
"""

import re
from pathlib import Path

import fitz

QUESTION_RE = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,3})[\.\)]\s*")
SECTION_RE = re.compile(r"^\s*(PHYSICS|CHEMISTRY|MATHEMATICS|BIOLOGY|BOTANY|ZOOLOGY)\s*$", re.I)
HEADER_RE = re.compile(r"^\s*(English\s+हिंदी|I\s+PUC\s+JEE\s*\(MAINS\)|Page\s+\d+)\s*$", re.I)


def _clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
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


def _span_record(span: dict) -> dict:
    return {
        "text": span.get("text", ""),
        "bbox": list(span.get("bbox", [0, 0, 0, 0])),
        "font": span.get("font", ""),
        "size": float(span.get("size", 0) or 0),
        "flags": int(span.get("flags", 0) or 0),
    }


def _is_math_span(span: dict) -> bool:
    font = str(span.get("font", ""))
    flags = int(span.get("flags", 0) or 0)
    return "math" in font.lower() or bool(flags & 1)


def _translation_source(lines: list[dict]) -> tuple[str, list[str]]:
    math_values: list[str] = []
    parts: list[str] = []
    for line in lines:
        line_parts: list[str] = []
        for span in line.get("spans", []):
            value = span.get("text", "")
            if not value:
                continue
            if _is_math_span(span):
                token = f"__MATH_{len(math_values)}__"
                math_values.append(value)
                line_parts.append(token)
            else:
                line_parts.append(value)
        value = "".join(line_parts).strip()
        if value:
            parts.append(value)
    return _clean_text("\n".join(parts)), math_values


def _line_text(line: dict) -> str:
    return "".join(s.get("text", "") for s in line.get("spans", [])).strip()


def _split_text_block(block: dict) -> list[dict]:
    """Split a PDF text block only at real question-start lines.

    Unlike the old implementation, this preserves the original rich lines and
    math metadata for every resulting question.
    """
    lines = block.get("lines", [])
    if not lines:
        return [block]

    chunks: list[list[dict]] = []
    current: list[dict] = []
    saw_question = False
    for line in lines:
        text = _line_text(line)
        is_q = bool(QUESTION_RE.match(text))
        if is_q and saw_question and current:
            chunks.append(current)
            current = []
        if is_q:
            saw_question = True
        current.append(line)
    if current:
        chunks.append(current)

    if len(chunks) == 1:
        return [block]

    result = []
    for chunk in chunks:
        text = _clean_text("\n".join(_line_text(line) for line in chunk if _line_text(line)))
        if not text:
            continue
        source, math_values = _translation_source(chunk)
        first = chunk[0].get("bbox", block["bbox"])
        last = chunk[-1].get("bbox", block["bbox"])
        copied = dict(block)
        copied["text"] = text
        copied["translation_source"] = source or text
        copied["math_values"] = math_values
        copied["lines"] = chunk
        copied["bbox"] = [
            min(first[0], last[0]),
            min(first[1], last[1]),
            max(first[2], last[2]),
            max(first[3], last[3]),
        ]
        result.append(copied)
    return result


def _mark_math_visual(block: dict, page: fitz.Page, image_root: Path, page_no: int, block_index: int) -> None:
    """Create a visual fallback only for complex math lines.

    Word math is attempted first by docx_generator. This image is retained only
    when a PDF formula is too complex to reconstruct reliably from text spans.
    """
    for line_index, line in enumerate(block.get("lines", [])):
        spans = line.get("spans", [])
        math_spans = [s for s in spans if _is_math_span(s)]
        if not math_spans:
            continue
        x0, y0, x1, y1 = line.get("bbox", block["bbox"])
        # Only make a fallback for unusually tall/stacked math, such as a
        # fraction or radical whose numerator/denominator occupy separate rows.
        ys = [round((s["bbox"][1] + s["bbox"][3]) / 2, 1) for s in math_spans]
        if max(ys) - min(ys) < 5:
            continue
        clip = fitz.Rect(max(0, x0 - 2), max(0, y0 - 2), min(page.rect.width, x1 + 2), min(page.rect.height, y1 + 2))
        if clip.width < 20 or clip.height < 8:
            continue
        path = image_root / f"page_{page_no}_math_{block_index}_{line_index}.png"
        page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False).save(path)
        line["math_visual_path"] = str(path)


def _rect_close(a: fitz.Rect, b: fitz.Rect, gap: float = 14) -> bool:
    return not (a.x1 + gap < b.x0 or b.x1 + gap < a.x0 or a.y1 + gap < b.y0 or b.y1 + gap < a.y0)


def _drawing_clusters(page) -> list[fitz.Rect]:
    rects = []
    for drawing in page.get_drawings():
        r = fitz.Rect(drawing.get("rect", (0, 0, 0, 0)))
        if r.width < 2 or r.height < 2:
            continue
        if r.width > page.rect.width * 0.82 and r.height < 12:
            continue
        if r.height > page.rect.height * 0.82 and r.width < 12:
            continue
        rects.append(r)
    clusters: list[fitz.Rect] = []
    for rect in rects:
        for i, current in enumerate(clusters):
            if _rect_close(current, rect):
                clusters[i] = current | rect
                break
        else:
            clusters.append(rect)
    changed = True
    while changed:
        changed = False
        for i in range(len(clusters) - 1, -1, -1):
            for j in range(i - 1, -1, -1):
                if _rect_close(clusters[i], clusters[j]):
                    clusters[j] = clusters[j] | clusters[i]
                    clusters.pop(i)
                    changed = True
                    break
            if changed:
                break
    return [r for r in clusters if r.width >= 45 and r.height >= 25 and r.width * r.height >= 1400]


def _render_drawing(page, rect: fitz.Rect, image_root: Path, index: int):
    pad = 6
    crop = fitz.Rect(max(0, rect.x0 - pad), max(0, rect.y0 - pad), min(page.rect.width, rect.x1 + pad), min(page.rect.height, rect.y1 + pad))
    path = image_root / f"page_{page.number + 1}_vector_{index}.png"
    page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=crop, alpha=False).save(str(path))
    return path, crop


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
            if block.get("type") == 1:
                width, height = x1 - x0, y1 - y0
                if y0 > 55 and width > 45 and height > 35 and block.get("image"):
                    ext = block.get("ext", "png")
                    image_path = image_root / f"page_{page_no}_image_{block_index}.{ext}"
                    image_path.write_bytes(block["image"])
                    blocks.append({
                        "page": page_no, "type": "image", "text": "", "english": "", "hindi": "",
                        "bbox": bbox, "column": _column_key(x0, page.rect.width),
                        "image_path": str(image_path), "width": width, "height": height,
                    })
                continue
            if block.get("type") != 0:
                continue

            rich_lines = []
            plain_lines = []
            for line in block.get("lines", []):
                spans = [_span_record(s) for s in line.get("spans", []) if s.get("text")]
                if not spans:
                    continue
                rich_lines.append({"bbox": list(line.get("bbox", [0, 0, 0, 0])), "spans": spans})
                plain_lines.append("".join(s["text"] for s in spans))
            text = _clean_text("\n".join(plain_lines))
            if not text or HEADER_RE.fullmatch(text):
                continue

            translation_source, math_values = _translation_source(rich_lines)
            base = {
                "page": page_no, "type": "text", "text": text,
                "translation_source": translation_source or text,
                "math_values": math_values, "lines": rich_lines, "bbox": bbox,
                "column": _column_key(x0, page.rect.width),
                "font_size": max([s["size"] for l in rich_lines for s in l["spans"]] or [0]),
            }
            for piece in _split_text_block(base):
                _mark_math_visual(piece, page, image_root, page_no, block_index)
                blocks.append(piece)

        for drawing_index, rect in enumerate(_drawing_clusters(page)):
            duplicate = False
            for existing in blocks:
                if existing.get("type") != "image":
                    continue
                eb = fitz.Rect(existing.get("bbox", [0, 0, 0, 0]))
                inter = rect & eb
                if inter.get_area() > 0.75 * min(rect.get_area(), eb.get_area()):
                    duplicate = True
                    break
            if duplicate:
                continue
            image_path, crop = _render_drawing(page, rect, image_root, drawing_index)
            blocks.append({
                "page": page_no, "type": "image", "text": "", "english": "", "hindi": "",
                "bbox": [crop.x0, crop.y0, crop.x1, crop.y1],
                "column": _column_key(crop.x0, page.rect.width),
                "image_path": str(image_path), "width": crop.width, "height": crop.height,
            })

        blocks.sort(key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
        pages.append({"page": page_no, "width": page.rect.width, "height": page.rect.height, "blocks": blocks})

    return {"pages": pages, "source_pdf": str(pdf_path)}
