from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.services.pdf_parser import extract_document
from backend.services.translator import DemoTranslator
from backend.services.docx_generator import generate_docx

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(title="Bilingual Paper Generator", version="0.2.0")
app.mount("/static", StaticFiles(directory=str(BASE / "frontend")), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "frontend" / "index.html").read_text(encoding="utf-8")


@app.post("/api/generate")
async def generate(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    translator = DemoTranslator()
    if not translator.client:
        raise HTTPException(
            500,
            "GEMINI_API_KEY is not configured. Add it to the project's .env file and restart the server.",
        )

    job = uuid4().hex
    pdf_path = UPLOADS / f"{job}.pdf"
    docx_path = OUTPUTS / f"bilingual_{job}.docx"

    pdf_path.write_bytes(await file.read())

    document = extract_document(pdf_path)
    bilingual = translator.translate_document(document)
    generate_docx(bilingual, docx_path)

    return {
        "job_id": job,
        "pages": len(document["pages"]),
        "questions": len(bilingual["questions"]),
        "download": f"/api/download/{docx_path.name}",
        "note": "Generated with Gemini NCERT-style Hindi translation and layout-aware Word output.",
    }


@app.get("/api/download/{filename}")
def download(filename: str):
    path = OUTPUTS / filename
    if not path.exists():
        raise HTTPException(404, "File not found.")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )
