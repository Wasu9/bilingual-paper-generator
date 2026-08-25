from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
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

app = FastAPI(title="Bilingual Paper Generator", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE / "frontend")), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "frontend" / "index.html").read_text(encoding="utf-8")


@app.post("/api/generate")
async def generate(
    file: UploadFile = File(...),
    gemini_api_key: str = Form(default=""),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    translator = DemoTranslator(api_key=gemini_api_key)
    if not translator.client:
        raise HTTPException(
            400,
            "Hindi translation is not configured. Enter your Gemini API key in the app or set GEMINI_API_KEY in .env.",
        )

    job = uuid4().hex
    pdf_path = UPLOADS / f"{job}.pdf"
    docx_path = OUTPUTS / f"bilingual_{job}.docx"
    pdf_path.write_bytes(await file.read())

    try:
        document = extract_document(pdf_path)
        if not document.get("pages"):
            raise RuntimeError("No readable pages were found in the PDF.")

        bilingual = translator.translate_document(document)
        if not bilingual.get("questions"):
            raise RuntimeError("No questions were detected. The PDF layout could not be parsed.")

        generate_docx(bilingual, docx_path)
    except RuntimeError as exc:
        raise HTTPException(502, f"Paper generation failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Paper generation failed: {exc}") from exc

    return {
        "job_id": job,
        "pages": len(document["pages"]),
        "questions": len(bilingual["questions"]),
        "download": f"/api/download/{docx_path.name}",
        "note": "Professional continuous paper: English + Hindi, preserved figures, Word math objects where source math spans are available, and page-safe question blocks.",
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
