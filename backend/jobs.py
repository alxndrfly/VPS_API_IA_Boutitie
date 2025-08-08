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

# ---- Temporary demo processor (replace with real logic later) ----
async def _demo_process(job_id: str, files: List[UploadFile]):
    await job_store.push(job_id, {"event": "started"})
    total = len(files)
    for i, f in enumerate(files, start=1):
        await job_store.push(job_id, {
            "event": "progress",
            "file": getattr(f, "filename", f"file_{i}"),
            "current": i,
            "total": total
        })
        await asyncio.sleep(0.5)  # simulate work
    await job_store.push(job_id, {"event": "done"})
    job_store.mark_done(job_id)

async def start_processing(job_id: str, files: List[UploadFile]):
    # TODO: Wire your real app_logic here later.
    await _demo_process(job_id, files)
