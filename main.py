from fastapi import FastAPI, UploadFile, File, HTTPException, status, Depends, Header, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict
import os, asyncio, json
from dotenv import load_dotenv
from backend.jobs import job_store, start_processing


app = FastAPI(title="IA-Avocats API", version="0.1")

# -----------------------------
# CORS configuration
# -----------------------------
origins = [
    "https://avocats.velvet-ia.com",  # Your deployed frontend
    "http://localhost:5173",          # Local dev (optional)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers, including x-api-key
)

# -----------------------------
# Load environment variables
# -----------------------------
load_dotenv()
API_KEY = os.getenv("API_KEY")

# Plain API key checker (no FastAPI params here)
def check_api_key(key: str | None):
    print(f"[check_api_key] got={key!r} expected={API_KEY!r}")  # TEMP DEBUG
    if key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key"
        )


# API key verification (header OR query param for SSE)
def verify_api_key(
    x_api_key: str | None = Header(default=None),
    api_key_qs: str | None = None,  # pulled from query via dependency below when used
):
    key = x_api_key or api_key_qs
    print(f"[verify_api_key] got={key!r} expected={API_KEY!r}")  # <-- TEMP DEBUG
    if key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key"
        )

# -----------------------------
# Routes
# -----------------------------
@app.get("/health", tags=["meta"])
def health() -> Dict[str, str]:
    """Simple liveness probe for VPS / uptime checks."""
    return {"status": "ok"}


# Example protected route using dependency injection
@app.get("/some-protected-route", dependencies=[Depends(verify_api_key)])
def some_protected_route():
    return {"message": "This route requires a valid API key."}

# ---------------------------------
# Background job summary endpoints
# ---------------------------------
@app.post("/summaries/start")
async def summaries_start(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    x_api_key: str | None = Header(default=None),
):
    check_api_key(x_api_key)  # <-- validate
    job_id = job_store.create_job()
    background_tasks.add_task(start_processing, job_id, files)
    return {"job_id": job_id}


# GET uses query param (EventSource can't send headers)
@app.get("/summaries/stream")
async def summaries_stream(
    job_id: str,
    api_key: str | None = Query(default=None, alias="api_key"),
):
    check_api_key(api_key)  # <-- validate

    if not job_store.exists(job_id):
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def event_source():
        yield "retry: 2000\n\n"
        q = job_store.get_queue(job_id)
        while True:
            item = await q.get()
            yield f"data:{json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("event") == "done":
                break

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_source(), media_type="text/event-stream", headers=headers)