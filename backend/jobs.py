# backend/jobs.py
import asyncio, json, uuid, time, threading
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
    """
    Run the sync progress generator and forward items to the SSE queue in real time.
    Keeps your original payload shape: 'pct'/'msg' for progress, 'result' for final data.
    """
    from backend.app_logic import process_uploaded_files

    try:
        await job_store.push(job_id, {"event": "started", "ts": time.time()})

        # Adapt input to what app_logic expects (.name and .read())
        adapted_files = [InMemoryUpload(f["filename"], f["content"]) for f in files]

        # Cross-thread handoff queue
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def worker():
            """Run the synchronous generator and ship items to the asyncio queue."""
            try:
                for payload in process_uploaded_files(adapted_files):
                    asyncio.run_coroutine_threadsafe(q.put(payload), loop)
                asyncio.run_coroutine_threadsafe(q.put({"__end__": True}), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(q.put({"__error__": str(e)}), loop)

        threading.Thread(target=worker, daemon=True).start()

        # Consume items as they arrive and forward to the SSE queue
        while True:
            item = await q.get()

            if "__error__" in item:
                await job_store.push(job_id, {"event": "error", "detail": item["__error__"]})
                break

            if "__end__" in item:
                break

            if "pct" in item or "msg" in item:
                # Your original progress shape
                await job_store.push(job_id, {"event": "progress", **item})
                await asyncio.sleep(0)  # yield so StreamingResponse can flush now

            elif "result" in item:
                await job_store.push(job_id, {"event": "result", "data": item["result"]})
                await asyncio.sleep(0)

            else:
                # Unknown payloads won't crash the stream; they show up for debugging
                await job_store.push(job_id, {"event": "progress", "debug": item})
                await asyncio.sleep(0)

        await job_store.push(job_id, {"event": "done", "ts": time.time()})
        job_store.mark_done(job_id)

    except Exception as exc:
        await job_store.push(job_id, {"event": "error", "detail": str(exc)})
        await job_store.push(job_id, {"event": "done"})
        job_store.mark_done(job_id)