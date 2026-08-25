from pathlib import Path
import re

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

TOKEN_RE = re.compile(r"__MATH_(\d+)__")
QUESTION_RE = re.compile(r"^(\s*(?:Q\.?\s*)?\d{1,3}[\.\)])\s*(.*)$", re.S)


def _is_hindi(text: str) -> bool:
    return any("\u0900" <= c <= "\u097F" for c in str(text or ""))


def _set_run_font(run, text, size=9.7, bold=False, font=None):
    run.bold = bold
    run.font.name = font or ("Noto Sans Devanagari" if _is_hindi(text) else "Times New Roman")
    run.font.size = Pt(size)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), run.font.name)
    rfonts.set(qn("w:hAnsi"), run.font.name)
    rfonts.set(qn("w:eastAsia"), run.font.name)
    rfonts.set(qn("w:cs"), run.font.name)


def _set_cell_margins(cell, top=50, start=85, bottom=50, end=85):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for side, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _prevent_row_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _repeat_header_row(row):
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:tblHeader")) is None:
        tr_pr.append(OxmlElement("w:tblHeader"))


def _set_table_layout_fixed(table):
    tbl_pr = table._tbl.tblPr
    layout = tbl_pr.first_child_found_in("w:tblLayout")
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")


def _set_table_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            borders.append(node)
        node.set(qn("w:val"), "single" if side in ("top", "left", "bottom", "right", "insideV") else "nil")
        node.set(qn("w:sz"), "5")
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), "A6A6A6")


def _clear_cell(cell):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True
    return p


def _add_rich_line(cell, line):
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(1.3)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True
    for span in line.get("spans", []):
        text = span.get("text", "")
        if not text:
            continue
        font = "Cambria Math" if "math" in str(span.get("font", "")).lower() else "Times New Roman"
        run = p.add_run(text)
        flags = int(span.get("flags", 0) or 0)
        _set_run_font(run, text, size=min(max(float(span.get("size", 10) or 10), 8), 11), font=font)
        run.italic = bool(flags & 2)
        run.bold = bool(flags & 16)
        if flags & 1:
            run.font.superscript = True
    return p


def _add_plain_with_math_tokens(cell, text, math_values, bold_number=False):
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(1.3)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True
    text = str(text or "")
    pos = 0
    for match in TOKEN_RE.finditer(text):
        if match.start() > pos:
            part = text[pos:match.start()]
            run = p.add_run(part)
            _set_run_font(run, part, bold=bold_number and pos == 0)
        idx = int(match.group(1))
        value = math_values[idx] if 0 <= idx < len(math_values) else ""
        run = p.add_run(value)
        _set_run_font(run, value, font="Cambria Math")
        pos = match.end()
    if pos < len(text):
        part = text[pos:]
        run = p.add_run(part)
        _set_run_font(run, part, bold=bold_number and pos == 0)
    return p


def _add_picture(cell, image_path, width=3.05):
    if not image_path or not Path(image_path).exists():
        return
    p = cell.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.keep_together = True
    p.add_run().add_picture(image_path, width=Inches(width))


def _add_visual_content(cell, blocks, language_key):
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    for block in sorted(blocks, key=lambda b: (float((b.get("bbox") or [0, 0, 0, 0])[1]), float((b.get("bbox") or [0, 0, 0, 0])[0]))):
        if block.get("type") == "image":
            _add_picture(cell, block.get("image_path"), min(3.2, max(1.2, float(block.get("width", 250)) / 100)))
            continue

        if language_key == "english" and block.get("lines"):
            for line in block["lines"]:
                if line.get("math_visual_path"):
                    _add_picture(cell, line["math_visual_path"], 3.05)
                else:
                    _add_rich_line(cell, line)
            continue

        text = str(block.get(language_key, "") or "").strip()
        if not text:
            continue

        if language_key == "hindi" and block.get("lines") and "\n" in text:
            translated_lines = [line.strip() for line in text.splitlines()]
            for idx, source_line in enumerate(block["lines"]):
                translated_line = translated_lines[idx] if idx < len(translated_lines) else ""
                if source_line.get("math_visual_path"):
                    _add_picture(cell, source_line["math_visual_path"], 3.05)
                elif translated_line:
                    _add_plain_with_math_tokens(cell, translated_line, block.get("math_values", []), bold_number=bool(QUESTION_RE.match(translated_line)))
            continue

        _add_plain_with_math_tokens(cell, text, block.get("math_values", []), bold_number=bool(QUESTION_RE.match(text)))


def _shade_cell(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def _add_full_width_heading(table, text):
    row = table.add_row()
    _prevent_row_split(row)
    cell = row.cells[0].merge(row.cells[1])
    _clear_cell(cell)
    _shade_cell(cell, "E7E6E6")
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    _set_run_font(r, text, size=11, bold=True)


def generate_docx(data: dict, output_path: Path):
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.45)
    section.bottom_margin = Inches(0.45)
    section.left_margin = Inches(0.45)
    section.right_margin = Inches(0.45)
    section.header_distance = Inches(0.2)
    section.footer_distance = Inches(0.2)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(1)
    r = p.add_run("BILINGUAL QUESTION PAPER")
    _set_run_font(r, r.text, size=15, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(5)
    r = p.add_run("English  |  हिंदी")
    _set_run_font(r, r.text, size=10, bold=True)

    table = doc.add_table(rows=1, cols=2)
    table.autofit = False
    _set_table_layout_fixed(table)
    _set_table_borders(table)

    header = table.rows[0]
    for cell, label in zip(header.cells, ("English", "हिंदी")):
        _clear_cell(cell)
        _shade_cell(cell, "D9EAF7")
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(label)
        _set_run_font(r, label, size=10.5, bold=True)
        _set_cell_margins(cell, top=60, start=80, bottom=60, end=80)
    _prevent_row_split(header)
    _repeat_header_row(header)

    last_section = None
    for question in data.get("questions", []):
        section_name = question.get("section")
        if section_name and section_name != last_section:
            _add_full_width_heading(table, section_name.upper())
            last_section = section_name

        row = table.add_row()
        _prevent_row_split(row)
        english_cell, hindi_cell = row.cells
        _set_cell_margins(english_cell)
        _set_cell_margins(hindi_cell)
        _clear_cell(english_cell)
        _clear_cell(hindi_cell)
        blocks = question.get("blocks", [])
        _add_visual_content(english_cell, blocks, "english")
        _add_visual_content(hindi_cell, blocks, "hindi")

    for row in table.rows:
        row.cells[0].width = Inches(3.72)
        row.cells[1].width = Inches(3.72)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Shaheen Group of Institutions  •  Bilingual Question Paper  •  ")
    _set_run_font(run, run.text, size=8)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    footer._p.append(fld)

    doc.save(output_path)
