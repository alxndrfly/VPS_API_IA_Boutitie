# backend/jobs.py
import asyncio, json, uuid
from typing import Dict, Any, List
from fastapi import UploadFile

class JobStore:
    def __init__(self):
        self.queues: Dict[str, asyncio.Queue] = {}
        self.done: Dict[str, bool] = {}

    def create_job(self) -> str:
        job_id = uuid.uuid4().hex
        self.queues[job_id] = asyncio.Queue()
        self.done[job_id] = False
        return job_id

    def exists(self, job_id: str) -> bool:
        return job_id in self.queues

    def get_queue(self, job_id: str) -> asyncio.Queue:
        return self.queues[job_id]

    async def push(self, job_id: str, data: Dict[str, Any]):
        # Always push JSON-serializable dicts
        await self.queues[job_id].put(data)

    def mark_done(self, job_id: str):
        self.done[job_id] = True

job_store = JobStore()

class InMemoryUpload:
    """Lightweight wrapper to mimic the subset of interface used by app_logic.

    Provides `.name` and a synchronous `.read()` returning bytes.
    """

    def __init__(self, filename: str, content: bytes):
        self.name = filename
        self._content = content

    def read(self) -> bytes:
        return self._content


async def start_processing(job_id: str, files: List[Dict[str, Any]]):
    """Process uploaded files and stream progress/events to the job queue.

    `files` should be a list of dicts: {"filename": str, "content": bytes}
    """
    from backend.app_logic import process_uploaded_files

    try:
        await job_store.push(job_id, {"event": "started"})

        # Adapt incoming payload into objects expected by app_logic
        adapted_files = [InMemoryUpload(f["filename"], f["content"]) for f in files]

        # Iterate the generator from app_logic and forward progress/result
        for payload in process_uploaded_files(adapted_files):
            if "pct" in payload or "msg" in payload:
                await job_store.push(job_id, {
                    "event": "progress",
                    **payload,
                })
            elif "result" in payload:
                await job_store.push(job_id, {
                    "event": "result",
                    "data": payload["result"],
                })

        await job_store.push(job_id, {"event": "done"})
        job_store.mark_done(job_id)

    except Exception as exc:
        # Surface an error event to the stream consumers
        await job_store.push(job_id, {"event": "error", "detail": str(exc)})
        await job_store.push(job_id, {"event": "done"})
        job_store.mark_done(job_id)
