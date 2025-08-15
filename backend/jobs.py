# backend/jobs.py
import asyncio, json, uuid, time, threading, base64
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

    def getvalue(self) -> bytes:
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









async def start_pdf_to_word(job_id: str, files: List[Dict[str, Any]]):
    """
    Convert ONE uploaded PDF to DOCX and stream progress.
    Emits:
      started → progress (10 %, 85 %) → result (base64) → done
    """
    from backend.app_logic import convert_pdf_to_word

    try:
        await job_store.push(job_id, {"event": "started", "ts": time.time()})

        if len(files) != 1:
            await job_store.push(job_id, {"event": "error",
                                          "detail": "Exactly one PDF expected"})
            await job_store.push(job_id, {"event": "done"})
            job_store.mark_done(job_id)
            return

        upload = InMemoryUpload(files[0]["filename"], files[0]["content"])

        await job_store.push(job_id, {"event": "progress", "pct": 10,
                                      "msg": "Conversion en cours…"})

        loop = asyncio.get_running_loop()
        word_buf = await loop.run_in_executor(
            None, lambda: convert_pdf_to_word(upload)
        )

        if not word_buf:
            await job_store.push(job_id, {"event": "error",
                                          "detail": "Adobe API failed"})
            await job_store.push(job_id, {"event": "done"})
            job_store.mark_done(job_id)
            return

        await job_store.push(job_id, {"event": "progress", "pct": 85,
                                      "msg": "Encodage du DOCX…"})

        import base64, os
        b64 = base64.b64encode(word_buf.getvalue()).decode()
        out_name = os.path.splitext(upload.name)[0] + ".docx"

        await job_store.push(job_id, {
            "event": "result",
            "data": {
                "filename": out_name,
                "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "base64": b64
            }
        })

        await job_store.push(job_id, {"event": "done", "ts": time.time()})
        job_store.mark_done(job_id)

    except Exception as exc:
        await job_store.push(job_id, {"event": "error", "detail": str(exc)})
        await job_store.push(job_id, {"event": "done"})
        job_store.mark_done(job_id)









async def start_doc_resume(job_id: str, files: List[Dict[str, Any]]):
    """
    Generate a single-document résumé (PDF or Word) and stream progress.

    Emits:
      started → progress (10 %, 85 %) → result {filename, mime, base64, text} → done
    """
    from backend.app_logic import (
        create_single_document_summary,
        create_summary_word_document,
        InMemoryUpload,  # re-use existing wrapper
    )

    try:
        await job_store.push(job_id, {"event": "started", "ts": time.time()})

        if len(files) != 1:
            await job_store.push(job_id, {"event": "error",
                                          "detail": "Exactly one document expected"})
            await job_store.push(job_id, {"event": "done"})
            job_store.mark_done(job_id)
            return

        upload = InMemoryUpload(files[0]["filename"], files[0]["content"])

        await job_store.push(job_id, {"event": "progress", "pct": 10,
                                      "msg": "Génération du résumé…"})

        # Run blocking summariser in a thread pool
        loop = asyncio.get_running_loop()
        summary_text: str | None = await loop.run_in_executor(
            None, lambda: create_single_document_summary(upload)
        )

        if not summary_text:
            await job_store.push(job_id, {"event": "error",
                                          "detail": "Résumé échoué"})
            await job_store.push(job_id, {"event": "done"})
            job_store.mark_done(job_id)
            return

        # Build a .docx version
        word_buf = await loop.run_in_executor(
            None, lambda: create_summary_word_document(summary_text, upload.name)
        )

        await job_store.push(job_id, {"event": "progress", "pct": 85,
                                      "msg": "Encodage du DOCX…"})

        b64 = base64.b64encode(word_buf.getvalue()).decode("ascii")
        out_name = (upload.name.rsplit(".", 1)[0] or "resume") + ".docx"

        await job_store.push(job_id, {
            "event": "result",
            "data": {
                "filename": out_name,
                "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "base64": b64,
                "text": summary_text,
            }
        })

        await job_store.push(job_id, {"event": "done", "ts": time.time()})
        job_store.mark_done(job_id)

    except Exception as exc:
        await job_store.push(job_id, {"event": "error", "detail": str(exc)})
        await job_store.push(job_id, {"event": "done"})
        job_store.mark_done(job_id)
