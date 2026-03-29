import asyncio
import logging
import time
from dataclasses import dataclass

from app.api.routers.upload import _persist_csf
from app.db.session import SessionLocal
from app.services import audit_service, ingest, job_service

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DocumentTask:
    job_id: str
    company_id: str
    user_id: str
    document_type: str
    filename: str
    content: bytes
    validate_online: bool
    ip_address: str | None = None
    user_agent: str | None = None


def _process_document_task(task: DocumentTask) -> None:
    db = SessionLocal()
    t0 = time.monotonic()
    try:
        job_service.update_job_processing(db, task.job_id)

        ingest.save_upload(task.content, task.filename)
        data = ingest.process_pdf(task.content, task.company_id, validate_online=task.validate_online)
        data["source_filename"] = task.filename

        csf, _created = _persist_csf(data, db, pdf_bytes=task.content)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        job_service.update_job_done(db, task.job_id, csf.id, elapsed_ms)
        audit_service.create_audit_log(
            db,
            company_id=task.company_id,
            user_id=task.user_id,
            action="document_upload_complete",
            entity_type="document_job",
            entity_id=task.job_id,
            details={
                "status": "done",
                "document_id": csf.id,
                "error": None,
                "processing_time_ms": elapsed_ms,
            },
            ip_address=task.ip_address,
            user_agent=task.user_agent,
            status="success",
        )
    except Exception as exc:
        error_msg = str(exc)
        logger.exception("Error processing document job %s", task.job_id)
        job_service.update_job_failed(db, task.job_id, error_msg)
        audit_service.create_audit_log(
            db,
            company_id=task.company_id,
            user_id=task.user_id,
            action="document_upload_complete",
            entity_type="document_job",
            entity_id=task.job_id,
            details={
                "status": "failed",
                "document_id": None,
                "error": error_msg,
            },
            ip_address=task.ip_address,
            user_agent=task.user_agent,
            status="error",
            error_message=error_msg,
        )
    finally:
        db.close()


class InProcessDocumentQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[DocumentTask] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []

    async def start(self, worker_count: int = 1) -> None:
        if self._workers:
            return
        for index in range(worker_count):
            worker = asyncio.create_task(self._worker_loop(index + 1))
            self._workers.append(worker)

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._workers.clear()

    async def enqueue(self, task: DocumentTask) -> None:
        await self._queue.put(task)

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            task = await self._queue.get()
            try:
                logger.info("Worker %s processing job %s", worker_id, task.job_id)
                await asyncio.to_thread(_process_document_task, task)
            finally:
                self._queue.task_done()


document_queue = InProcessDocumentQueue()