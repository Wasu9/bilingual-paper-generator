from pathlib import Path
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH


def _set_cell_text(cell, text, bold=False):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text)
    run.bold = bold
    run.font.name = "Noto Sans Devanagari" if any("\u0900" <= c <= "\u097F" for c in text) else "Aptos"
    run.font.size = Pt(10)


def generate_docx(data: dict, output_path: Path):
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.5)
    section.bottom_margin = Inches(0.5)
    section.left_margin = Inches(0.5)
    section.right_margin = Inches(0.5)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = title.add_run("Bilingual Paper Generator")
    r.bold = True
    r.font.size = Pt(15)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("English | हिंदी").bold = True

    for question in data["questions"]:
        table = doc.add_table(rows=0, cols=2)
        table.style = "Table Grid"

        for block in question["blocks"]:
            cells = table.add_row().cells
            _set_cell_text(cells[0], block["english"])
            _set_cell_text(cells[1], block["hindi"])

        doc.add_paragraph()

    doc.save(output_path)
