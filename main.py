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

# API key verification (header OR query param for SSE)
def verify_api_key(
    x_api_key: str | None = Header(default=None),
    api_key_qs: str | None = None,  # pulled from query via dependency below when used
):
    key = x_api_key or api_key_qs
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
    _auth=Depends(verify_api_key), 
):
    job_id = job_store.create_job()
    # Launch the processing as a background task (non-blocking)
    background_tasks.add_task(start_processing, job_id, files)
    return {"job_id": job_id}


@app.get("/summaries/stream")
async def summaries_stream(
    job_id: str,
    api_key: str = Query(default=None, alias="api_key"),
):
    # Validate API key here (EventSource can't send headers)
    verify_api_key(api_key_qs=api_key)

    if not job_store.exists(job_id):
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def event_source():
        # Optional retry hint for clients
        yield "retry: 2000\n\n"
        q = job_store.get_queue(job_id)
        while True:
            item = await q.get()
            # IMPORTANT: Each SSE message ends with double \n
            yield f"data:{json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("event") == "done":
                break

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # harmless if not behind nginx
    }

    return StreamingResponse(event_source(), media_type="text/event-stream", headers=headers)