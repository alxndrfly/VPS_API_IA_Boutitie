# backend/jobs.py
import asyncio, json, uuid, time
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


async def start_processing(job_id: str, files: list[dict]):
    """Process PDFs and stream progress via job_store queue."""
    q = job_store.get_queue(job_id)

    # 1) announce start
    await q.put({"event": "started", "job_id": job_id, "ts": time.time()})
    await asyncio.sleep(0)  # yield control to flush

    total = len(files)
    for idx, f in enumerate(files, start=1):
        # 2) per-file start
        await q.put({
            "event": "progress",
            "job_id": job_id,
            "step": f"reading_file_{idx}",
            "message": f"L'IA traite les PDFs... ({idx}/{total})",
            "percent": int((idx - 1) / total * 100),
            "filename": f.get("filename"),
        })
        await asyncio.sleep(0)

        # --- your actual heavy work per file here ---
        # Example: simulate work with small async sleeps so the loop can flush
        for sub in range(1, 6):
            await asyncio.sleep(0.3)  # replace with real async I/O calls
            await q.put({
                "event": "progress",
                "job_id": job_id,
                "step": f"file_{idx}_chunk_{sub}",
                "message": f"Traitement {idx}/{total} - étape {sub}/5",
                "percent": min(99, int(((idx - 1) + sub/5) / total * 100)),
            })

        # save any per-file result if you need to combine later
        # ...

    # 3) final result example — replace with your real summaries
    result_payload = {
        "event": "result",
        "job_id": job_id,
        "original_summary": "Résumé original combiné…",
        "chronological_summary": "Résumé chronologique combiné…",
    }
    await q.put(result_payload)
    await asyncio.sleep(0)

    # 4) done
    await q.put({"event": "done", "job_id": job_id, "ts": time.time()})
    await asyncio.sleep(0)