from fastapi import FastAPI, UploadFile, File, Request, HTTPException, status, Depends
from fastapi.responses import StreamingResponse
from typing import List, Dict
import backend.app_logic as logic
import json, asyncio
from dotenv import load_dotenv
import os

app = FastAPI(title="IA-Avocats API", version="0.1")

# Load environment variables from .env file
load_dotenv()
API_KEY = os.getenv("API_KEY")

# Reusable API key dependency (for future non-streaming routes)
def verify_api_key(request: Request):
    client_key = request.headers.get("x-api-key")
    if client_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key"
        )

@app.get("/health", tags=["meta"])
def health() -> Dict[str, str]:
    """Simple liveness probe for VPS / uptime checks."""
    return {"status": "ok"}

@app.post("/summaries/stream", tags=["resumé"])
async def summaries_stream(request: Request, files: List[UploadFile] = File(...)):
    """
    SSE endpoint – streams {"pct":n,"msg":txt} until 100,
    then a final  {"result": {...}} payload.
    Front-end listens with EventSource.
    """
    # Inline API key check — necessary because middleware or dependencies
    # can be bypassed on StreamingResponse endpoints
    client_key = request.headers.get("x-api-key")
    if client_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")

    # Pass UploadFile objects straight to the generator
    gen = logic.process_uploaded_files(files)

    async def event_source():
        for chunk in gen:
            yield f"data:{json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)  # yield to event loop

    return StreamingResponse(event_source(), media_type="text/event-stream")
