from pathlib import Path
from uuid import uuid4
import threading

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, UploadFile, File, HTTPException, Form
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

# Jobs are intentionally kept in memory for the Codespaces app. The important
# change is that the browser no longer waits for Gemini + DOCX generation in
# one long HTTP request, which was causing the Codespaces proxy to return 504.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **values):
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(values)


def _run_job(job_id: str, pdf_path: Path, docx_path: Path, api_key: str):
    try:
        _set_job(job_id, status="processing", progress=10, message="Reading PDF...")
        translator = DemoTranslator(api_key=api_key)
        if not translator.client:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        document = extract_document(pdf_path)
        if not document.get("pages"):
            raise RuntimeError("No readable pages were found in the PDF.")

        _set_job(
            job_id,
            progress=25,
            message=f"PDF read: {len(document['pages'])} page(s). Translating questions...",
            pages=len(document["pages"]),
        )
        bilingual = translator.translate_document(document)
        if not bilingual.get("questions"):
            raise RuntimeError("No questions were detected. The PDF layout could not be parsed.")

        _set_job(
            job_id,
            progress=85,
            message=f"Hindi translation complete: {len(bilingual['questions'])} question(s). Building Word file...",
            questions=len(bilingual["questions"]),
        )
        generate_docx(bilingual, docx_path)

        _set_job(
            job_id,
            status="done",
            progress=100,
            message="Bilingual Word file is ready.",
            pages=len(document["pages"]),
            questions=len(bilingual["questions"]),
            download=f"/api/download/{docx_path.name}",
        )
    except Exception as exc:
        _set_job(job_id, status="error", progress=0, message=f"Paper generation failed: {exc}")
    finally:
        try:
            pdf_path.unlink(missing_ok=True)
        except Exception:
            pass


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "frontend" / "index.html").read_text(encoding="utf-8")


@app.post("/api/generate")
async def generate(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    gemini_api_key: str = Form(default=""),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    # Use the supplied key when present; otherwise DemoTranslator will use .env.
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

    with JOBS_LOCK:
        JOBS[job] = {
            "status": "queued",
            "progress": 0,
            "message": "Job queued. Starting PDF processing...",
        }

    # The long-running Gemini/DOCX work happens after this request returns.
    # This prevents the Codespaces HTTP proxy from timing out with 504.
    background_tasks.add_task(_run_job, job, pdf_path, docx_path, translator.api_key)

    return {"job_id": job, "status": "queued", "status_url": f"/api/jobs/{job}"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return {"job_id": job_id, **job}


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
