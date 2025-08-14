# main.py — minimal “new flow” API
from fastapi import FastAPI, UploadFile, File, HTTPException, status, Header, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Optional
import os, asyncio, json, time
from dotenv import load_dotenv
from backend.jobs import job_store, start_processing, start_pdf_to_word




app = FastAPI(title="IA-Avocats API", version="0.2")




# -----------------------------
# CORS
# -----------------------------
origins = [
    "https://avocats.velvet-ia.com",
    "http://localhost:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],  # includes x-api-key
)



# -----------------------------
# Auth
# -----------------------------
load_dotenv()
API_KEY = os.getenv("API_KEY")





def check_api_key(key: Optional[str]) -> None:
    if key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key"
        )




uploads_cache: Dict[str, List[Dict[str, bytes]]] = {}




@app.get("/health", tags=["meta"])
def health() -> Dict[str, str]:
    return {"status": "ok"}






@app.post("/jobs/new")
async def jobs_new(x_api_key: Optional[str] = Header(default=None)):
    """Create an empty job and return its id immediately."""
    check_api_key(x_api_key)
    job_id = job_store.create_job()
    uploads_cache[job_id] = []
    return {"job_id": job_id}





@app.post("/uploads/batch")
async def uploads_batch(
    job_id: str = Query(...),
    files: List[UploadFile] = File(...),
    x_api_key: Optional[str] = Header(default=None),
):
    """
    Accept all PDFs in one multipart request and buffer in memory per job_id.
    No disk writes; keeps confidentiality.
    """
    check_api_key(x_api_key)
    bucket = uploads_cache.get(job_id)
    if bucket is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    received = 0
    for f in files:
        content = await f.read()
        filename = getattr(f, "filename", getattr(f, "name", "upload.pdf"))
        bucket.append({"filename": filename, "content": content})
        received += 1

    return {"job_id": job_id, "received": received}





@app.post("/summaries/commit")
async def summaries_commit(
    background_tasks: BackgroundTasks,
    job_id: str = Query(...),
    x_api_key: Optional[str] = Header(default=None),
):
    """
    Start processing after all files are uploaded.
    Pulls the in-memory batch and enqueues start_processing(job_id, files).
    """
    check_api_key(x_api_key)
    buffered_files = uploads_cache.pop(job_id, None)
    if buffered_files is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")
    if not buffered_files:
        raise HTTPException(status_code=400, detail="No files uploaded for this job_id")

    background_tasks.add_task(start_processing, job_id, buffered_files)
    return {"job_id": job_id, "status": "queued"}






@app.post("/pdf2word/commit")
async def pdf2word_commit(
    background_tasks: BackgroundTasks,
    job_id: str = Query(...),
    x_api_key: Optional[str] = Header(default=None),
):
    """
    Start the PDF → Word conversion after upload.
    Accepts **exactly one** PDF buffered in uploads_cache[job_id].
    """
    check_api_key(x_api_key)
    buffered_files = uploads_cache.pop(job_id, None)
    if buffered_files is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")
    if len(buffered_files) != 1:
        raise HTTPException(status_code=400, detail="Exactly one PDF required")

    background_tasks.add_task(start_pdf_to_word, job_id, buffered_files)
    return {"job_id": job_id, "status": "queued"}





# GET uses query param (EventSource can't send headers)
@app.get("/summaries/stream")
async def summaries_stream(
    job_id: str,
    api_key: Optional[str] = Query(default=None, alias="api_key"),
):
    """SSE stream of progress/messages/results for a given job_id."""
    check_api_key(api_key)
    if not job_store.exists(job_id):
        raise HTTPException(status_code=404, detail="Unknown job_id")

    async def event_source():
        # Preamble to defeat proxy/client buffering
        yield "retry: 2000\n"
        yield ":" + (" " * 2048) + "\n\n"

        q = job_store.get_queue(job_id)
        last_beat = time.time()

        while True:
            try:
                # Wait up to 1s for the next item; if none, send a heartbeat
                item = await asyncio.wait_for(q.get(), timeout=1.0)
                # Proper SSE event with double newline
                payload = json.dumps(item, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                # Let the loop cycle so the transport flushes now
                await asyncio.sleep(0)

                if isinstance(item, dict) and item.get("event") == "done":
                    break

                last_beat = time.time()
            except asyncio.TimeoutError:
                # Heartbeat comment (keeps buffers open and flushing)
                yield f": hb {int(time.time())}\n\n"
                await asyncio.sleep(0)

    headers = {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_source(), media_type="text/event-stream", headers=headers)




@app.get("/pdf2word/stream")
async def pdf2word_stream(
    job_id: str,
    api_key: Optional[str] = Query(default=None, alias="api_key"),
):
    # Re-use the same SSE logic
    return await summaries_stream(job_id, api_key)