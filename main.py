# main.py  ── FastAPI entry-point for the “résumé” feature
#
# ▶ Run locally:   uvicorn main:app --reload --port 8000
# ▶ Swagger UI:    http://127.0.0.1:8000/docs
# ▶ Deployed:      systemd service ➜ nginx ➜ https://api.velvet-ia.com

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
from typing import List, Any, Dict
import backend.app_logic as logic
import json, asyncio

app = FastAPI(title="IA-Avocats API", version="0.1")

@app.get("/health", tags=["meta"])
def health() -> Dict[str, str]:
    """Simple liveness probe for VPS / uptime checks."""
    return {"status": "ok"}

@app.post("/summaries/stream", tags=["resumé"])
async def summaries_stream(files: List[UploadFile] = File(...)):
    """
    SSE endpoint – streams {"pct":n,"msg":txt} until 100,
    then a final  {"result": {...}} payload.
    Front-end listens with EventSource.
    """
    # Pass UploadFile objects straight to the generator
    gen = logic.process_uploaded_files(files)

    async def event_source():
        # Iterate sync generator in an async wrapper
        for chunk in gen:
            yield f"data:{json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)          # yield to event loop

    return StreamingResponse(event_source(),
                             media_type="text/event-stream")
