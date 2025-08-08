from fastapi import FastAPI, UploadFile, File, Request, HTTPException, status, Depends, Header
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

def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key"
        )

@app.get("/health", tags=["meta"])
def health() -> Dict[str, str]:
    """Simple liveness probe for VPS / uptime checks."""
    return {"status": "ok"}

@app.post("/summaries/stream", tags=["resumé"])
async def summaries_stream(
    x_api_key: str = Header(...),
    files: List[UploadFile] = File(...)
):
    # Reuse API key verification function inline here
    verify_api_key(x_api_key)

    gen = logic.process_uploaded_files(files)

    async def event_source():
        for chunk in gen:
            yield f"data:{json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)  # yield to event loop

    return StreamingResponse(event_source(), media_type="text/event-stream")

# Example protected route using dependency injection
@app.get("/some-protected-route", dependencies=[Depends(verify_api_key)])
def some_protected_route():
    return {"message": "This route requires a valid API key."}
