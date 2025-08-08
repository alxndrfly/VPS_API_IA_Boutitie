from fastapi import FastAPI, UploadFile, File, Request, HTTPException, status
from fastapi.responses import StreamingResponse
from typing import List, Any, Dict
import backend.app_logic as logic
import json, asyncio
from dotenv import load_dotenv
import os

app = FastAPI(title="IA-Avocats API", version="0.1")

# Load environment variables from .env file
load_dotenv()
API_KEY = os.getenv("API_KEY")

@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    # Paths allowed without API key
    public_paths = ["/docs", "/openapi.json", "/health"]

    if request.url.path not in public_paths:
        client_key = request.headers.get("x-api-key")
        if client_key != API_KEY:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API Key"
            )

    return await call_next(request)

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
