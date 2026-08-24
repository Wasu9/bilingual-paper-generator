# Bilingual Paper Generator

PDF → English + NCERT-style Hindi → editable Word (.docx)

## Goal
Generate bilingual exam papers while preserving the source paper's layout as closely as possible.

### Core rules
- English remains unchanged.
- Hindi is generated in NCERT-style terminology.
- Question numbering is preserved.
- A/B/C/D option columns are preserved when detected.
- Tables and multi-column layouts are represented structurally.
- Inline formulas are protected from translation.
- Standalone/big formulas are represented as Word math where the equation parser supports them.
- Fractions, superscripts and subscripts are not flattened into ordinary text.
- Output is an editable `.docx`, not an image.

## Current version
v0.1 is the project foundation:
- FastAPI backend
- simple browser upload UI
- PDF text/layout extraction with PyMuPDF
- block/column detection
- protected formula tokens
- translation-provider interface
- DOCX two-column bilingual output
- basic Word OMML equation support for common fractions/superscripts/subscripts

## Run locally

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env
uvicorn backend.app:app --reload
```

Open http://127.0.0.1:8000

## AI translation
Set one provider in `.env`.

The initial adapter is provider-neutral. The app currently includes a deterministic demo translator so the pipeline can be tested before an AI API key is added.

## Important
Perfect reconstruction of scanned/image PDFs and complex equations requires additional OCR/vision and equation-recognition stages. Those are deliberately isolated in `backend/services/` so they can be upgraded without rewriting the application.
