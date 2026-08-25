from pathlib import Path

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _is_hindi(text: str) -> bool:
    return any("\u0900" <= c <= "\u097F" for c in str(text or ""))


def _set_cell_text(cell, text, bold=False):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True
    run = p.add_run(str(text or ""))
    run.bold = bold
    run.font.name = "Noto Sans Devanagari" if _is_hindi(text) else "Aptos"
    run.font.size = Pt(10)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP


def _prevent_row_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _repeat_header_row(row):
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:tblHeader")) is None:
        tr_pr.append(OxmlElement("w:tblHeader"))


def _set_cell_margins(cell, top=55, start=75, bottom=55, end=75):
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


def _set_table_layout_fixed(table):
    tbl_pr = table._tbl.tblPr
    layout = tbl_pr.first_child_found_in("w:tblLayout")
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")


def _block_y(block):
    bbox = block.get("bbox") or [0, 0, 0, 0]
    return float(bbox[1])


def _group_blocks_by_visual_line(blocks):
    ordered = sorted(blocks, key=lambda b: (_block_y(b), (b.get("bbox") or [0])[0]))
    groups = []
    for block in ordered:
        y = _block_y(block)
        if not groups or abs(y - groups[-1]["y"]) > 5:
            groups.append({"y": y, "blocks": [block]})
        else:
            groups[-1]["blocks"].append(block)
    return [g["blocks"] for g in groups]


def _add_visual_content(parent_cell, blocks, language_key):
    groups = _group_blocks_by_visual_line(blocks)

    for group_index, group in enumerate(groups):
        columns = []
        for block in sorted(group, key=lambda b: (b.get("bbox") or [0])[0]):
            column = block.get("column", 0)
            if column not in columns:
                columns.append(column)

        if len(columns) == 1:
            text = "\n".join(
                str(b.get(language_key, "")) for b in group if b.get(language_key, "")
            )
            p = parent_cell.paragraphs[0] if group_index == 0 else parent_cell.add_paragraph()
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            p.paragraph_format.keep_together = True
            run = p.add_run(text)
            run.font.name = "Noto Sans Devanagari" if _is_hindi(text) else "Aptos"
            run.font.size = Pt(10)
            continue

        nested = parent_cell.add_table(rows=1, cols=len(columns))
        nested.style = "Table Grid"
        nested.autofit = False
        _set_table_layout_fixed(nested)
        column_index = {column: index for index, column in enumerate(columns)}

        for cell in nested.rows[0].cells:
            _set_cell_margins(cell, top=35, start=45, bottom=35, end=45)

        for block in sorted(group, key=lambda b: (b.get("bbox") or [0])[0]):
            idx = column_index[block.get("column", 0)]
            _set_cell_text(nested.rows[0].cells[idx], block.get(language_key, ""))

    parent_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP


def generate_docx(data: dict, output_path: Path):
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.45)
    section.bottom_margin = Inches(0.45)
    section.left_margin = Inches(0.45)
    section.right_margin = Inches(0.45)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(4)
    r = title.add_run("Bilingual Paper Generator")
    r.bold = True
    r.font.size = Pt(15)

    # One continuous table for the complete paper. It is created once so Word
    # can naturally flow it across pages instead of making a new box per question.
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    table.autofit = False
    _set_table_layout_fixed(table)

    header = table.rows[0]
    _set_cell_text(header.cells[0], "English", bold=True)
    _set_cell_text(header.cells[1], "हिंदी", bold=True)
    _prevent_row_split(header)
    _repeat_header_row(header)
    for cell in header.cells:
        _set_cell_margins(cell)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

    for question in data.get("questions", []):
        row = table.add_row()
        # Each question is exactly one outer-table row. Word moves that row to
        # the next page when it fits, preventing a question from being split.
        _prevent_row_split(row)

        english_cell, hindi_cell = row.cells
        _set_cell_margins(english_cell)
        _set_cell_margins(hindi_cell)

        blocks = question.get("blocks", [])
        _add_visual_content(english_cell, blocks, "english")
        _add_visual_content(hindi_cell, blocks, "hindi")

    for row in table.rows:
        row.cells[0].width = Inches(3.75)
        row.cells[1].width = Inches(3.75)

    doc.save(output_path)
