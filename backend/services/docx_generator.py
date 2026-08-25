from pathlib import Path
import re

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _is_hindi(text: str) -> bool:
    return any("\u0900" <= c <= "\u097F" for c in str(text or ""))


def _set_run_font(run, text, size=9.5, bold=False):
    run.bold = bold
    run.font.name = "Noto Sans Devanagari" if _is_hindi(text) else "Aptos"
    run.font.size = Pt(size)


def _set_cell_margins(cell, top=45, start=70, bottom=45, end=70):
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
    """Continuous table: outer border + English/Hindi divider, no box around every question."""
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
        node.set(qn("w:sz"), "6")
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), "B7B7B7")


def _clear_cell(cell):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True
    return p


def _add_text(parent_cell, text, bold_number=False, first=False):
    if first and not parent_cell.paragraphs[0].text:
        p = parent_cell.paragraphs[0]
    else:
        p = parent_cell.add_paragraph()

    p.paragraph_format.space_after = Pt(1.5)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True

    text = str(text or "").strip()
    if not text:
        return

    match = re.match(r"^(\s*(?:Q\.?\s*)?\d{1,3}[\.\)])\s*(.*)$", text, flags=re.S)
    if bold_number and match:
        number_run = p.add_run(match.group(1) + " ")
        _set_run_font(number_run, match.group(1), bold=True)
        body_run = p.add_run(match.group(2))
        _set_run_font(body_run, match.group(2))
    else:
        run = p.add_run(text)
        _set_run_font(run, text)


def _add_image(parent_cell, image_path, max_width=3.15):
    if not image_path or not Path(image_path).exists():
        return
    p = parent_cell.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_together = True
    run = p.add_run()
    run.add_picture(image_path, width=Inches(max_width))


def _sort_blocks(blocks):
    return sorted(
        blocks,
        key=lambda b: (
            float((b.get("bbox") or [0, 0, 0, 0])[1]),
            float((b.get("bbox") or [0, 0, 0, 0])[0]),
        ),
    )


def _add_visual_content(parent_cell, blocks, language_key):
    """Render one language in one cell without nested tables/boxes."""
    parent_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    first_text = True

    for block in _sort_blocks(blocks):
        if block.get("type") == "image":
            _add_image(parent_cell, block.get("image_path"))
            continue

        text = str(block.get(language_key, "") or "").strip()
        if not text:
            continue

        is_question = bool(re.match(r"^\s*(?:Q\.?\s*)?\d{1,3}[\.\)]", text))
        _add_text(parent_cell, text, bold_number=is_question, first=first_text)
        first_text = False


def generate_docx(data: dict, output_path: Path):
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.42)
    section.bottom_margin = Inches(0.42)
    section.left_margin = Inches(0.45)
    section.right_margin = Inches(0.45)

    # One continuous two-column table for the entire paper.
    # Each question is exactly one row and cantSplit keeps the complete question together.
    table = doc.add_table(rows=1, cols=2)
    table.autofit = False
    _set_table_layout_fixed(table)
    _set_table_borders(table)

    header = table.rows[0]
    _clear_cell(header.cells[0]).add_run("English")
    _clear_cell(header.cells[1]).add_run("हिंदी")
    for cell, label in zip(header.cells, ("English", "हिंदी")):
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in p.runs:
            _set_run_font(run, label, size=10, bold=True)
        _set_cell_margins(cell, top=55, start=70, bottom=55, end=70)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    _prevent_row_split(header)
    _repeat_header_row(header)

    for question in data.get("questions", []):
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
        row.cells[0].width = Inches(3.75)
        row.cells[1].width = Inches(3.75)

    doc.save(output_path)
