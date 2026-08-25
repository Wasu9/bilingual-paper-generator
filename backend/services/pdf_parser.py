"""Layout-aware PDF extraction for bilingual paper generation.

Preserves span-level typography (including Cambria Math and superscripts),
source figures, and enough layout metadata for a professional Word output.
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


def _is_question_only_number(text: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:Q\.?\s*)?\d{1,3}[\.\)]\s*", text or ""))


def _merge_question_number_blocks(blocks: list[dict]) -> list[dict]:
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
                merged["bbox"] = [min(b1[0], b2[0]), min(b1[1], b2[1]), max(b1[2], b2[2]), max(b1[3], b2[3])]
                merged["lines"] = list(block.get("lines", [])) + list(nxt.get("lines", []))
                result.append(merged)
                i += 2
                continue
        result.append(block)
        i += 1
    return result


def _span_record(span: dict) -> dict:
    return {"text": span.get("text", ""), "bbox": list(span.get("bbox", [0, 0, 0, 0])), "font": span.get("font", ""), "size": float(span.get("size", 0) or 0), "flags": int(span.get("flags", 0) or 0)}


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
        parts.append("".join(line_parts).strip())
    return _clean_text("\n".join(p for p in parts if p)), math_values


def _trim_rich_lines(lines: list[dict], char_limit: int) -> list[dict]:
    out = []
    used = 0
    for line in lines:
        line_text = "".join(s.get("text", "") for s in line.get("spans", []))
        if used > 0 and re.match(r"^\s*\d{1,3}\.\s+", line_text):
            break
        line_copy = {"bbox": list(line.get("bbox", [0, 0, 0, 0])), "spans": []}
        for span in line.get("spans", []):
            txt = span.get("text", "")
            remaining = char_limit - used
            if remaining <= 0:
                break
            if len(txt) <= remaining:
                line_copy["spans"].append(dict(span))
                used += len(txt)
            else:
                partial = dict(span)
                partial["text"] = txt[:remaining]
                line_copy["spans"].append(partial)
                used += remaining
                break
        if line_copy["spans"]:
            if line.get("math_visual_path") and used >= char_limit:
                line_copy["math_visual_path"] = line["math_visual_path"]
            out.append(line_copy)
        if used >= char_limit:
            break
    return out


def _split_embedded_question_blocks(blocks: list[dict]) -> list[dict]:
    out: list[dict] = []
    qstart_re = re.compile(r"(?<![\d(])\b(\d{1,3})\.\s+")
    for block in blocks:
        if block.get("type") != "text":
            out.append(block)
            continue
        text = block.get("text", "")
        matches = list(qstart_re.finditer(text))
        if not matches:
            out.append(block)
            continue
        prefix_end = matches[0].start()
        if prefix_end > 0:
            copied = dict(block)
            copied["text"] = text[:prefix_end].strip()
            copied["translation_source"] = copied["text"]
            copied["lines"] = _trim_rich_lines(block.get("lines", []), prefix_end)
            out.append(copied)
        for idx, match in enumerate(matches):
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            segment = text[match.start():end].strip()
            if not segment:
                continue
            copied = dict(block)
            copied["text"] = segment
            copied["translation_source"] = segment
            copied["lines"] = []
            copied["math_values"] = []
            out.append(copied)
    return out


def _mark_math_visual(block: dict, page: fitz.Page, image_root: Path, page_no: int, block_index: int) -> None:
    for line_index, line in enumerate(block.get("lines", [])):
        spans = line.get("spans", [])
        math_spans = [s for s in spans if _is_math_span(s)]
        if not math_spans:
            continue
        non_math = "".join(s.get("text", "") for s in spans if not _is_math_span(s)).strip()
        if len(non_math) > 28:
            continue
        x0, y0, x1, y1 = line.get("bbox", block["bbox"])
        clip = fitz.Rect(max(0, x0 - 2), max(0, y0 - 2), min(page.rect.width, x1 + 2), min(page.rect.height, y1 + 2))
        if clip.width < 20 or clip.height < 8:
            continue
        path = image_root / f"page_{page_no}_math_{block_index}_{line_index}.png"
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), clip=clip, alpha=False)
        pix.save(path)
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
    clusters = []
    for rect in rects:
        merged = False
        for i, current in enumerate(clusters):
            if _rect_close(current, rect):
                clusters[i] = current | rect
                merged = True
                break
        if not merged:
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
    pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=crop, alpha=False)
    image_path = image_root / f"page_{page.number + 1}_vector_{index}.png"
    pix.save(str(image_path))
    return image_path, crop


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
                    blocks.append({"page": page_no, "type": "image", "text": "", "english": "", "hindi": "", "bbox": bbox, "column": _column_key(x0, page.rect.width), "image_path": str(image_path), "width": width, "height": height})
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
            text_block = {"page": page_no, "type": "text", "text": text, "translation_source": translation_source or text, "math_values": math_values, "lines": rich_lines, "bbox": bbox, "column": _column_key(x0, page.rect.width), "font_size": max([s["size"] for l in rich_lines for s in l["spans"]] or [0])}
            _mark_math_visual(text_block, page, image_root, page_no, block_index)
            blocks.append(text_block)

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
            blocks.append({"page": page_no, "type": "image", "text": "", "english": "", "hindi": "", "bbox": [crop.x0, crop.y0, crop.x1, crop.y1], "column": _column_key(crop.x0, page.rect.width), "image_path": str(image_path), "width": crop.width, "height": crop.height})

        blocks.sort(key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
        blocks = _split_embedded_question_blocks(blocks)
        blocks = _merge_question_number_blocks(blocks)
        pages.append({"page": page_no, "width": page.rect.width, "height": page.rect.height, "blocks": blocks})

    return {"pages": pages, "source_pdf": str(pdf_path)}
