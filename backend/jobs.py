"""Background job store: in-memory state plus a JSON copy on disk.

Every job keeps its own directory under data/jobs/<id>/ and nothing there is
deleted afterwards, so the alignment, the Primer3 input and the results of a
run stay inspectable long after the browser tab is closed.
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import config

logger = logging.getLogger("jobs")

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_job(kind: str, meta: dict[str, Any] | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    workdir = config.JOBS_DIR / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "created": _now(),
            "updated": _now(),
            "log": [],
            "meta": meta or {},
            "result": None,
            "error": None,
            "workdir": str(workdir),
        }
    _persist(job_id)
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job:
            return json.loads(json.dumps(job, default=str))
    path = config.JOBS_DIR / job_id / "job.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def get_job_meta(job_id: str, log_from: int = 0) -> dict[str, Any] | None:
    """Status view without the result payload.

    Polled once a second while a job runs, so it must not copy the (possibly
    multi-megabyte) result document on every call.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            path = config.JOBS_DIR / job_id / "job.json"
            if not path.exists():
                return None
            job = json.loads(path.read_text())
        return {
            "id": job["id"],
            "kind": job["kind"],
            "status": job["status"],
            "stage": job["stage"],
            "progress": job["progress"],
            "created": job["created"],
            "updated": job["updated"],
            "error": job["error"],
            "log": list(job["log"][log_from:]),
            "log_total": len(job["log"]),
            "has_result": job["result"] is not None,
        }


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j["created"], reverse=True)
        return [{k: j[k] for k in
                 ("id", "kind", "status", "stage", "progress", "created", "meta")}
                for j in jobs[:limit]]


def update(job_id: str, **fields: Any) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated"] = _now()
    _persist(job_id)


def log(job_id: str, message: str, progress: int | None = None,
        stage: str | None = None) -> None:
    entry = {"time": _now(), "message": message}
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["log"].append(entry)
        if progress is not None:
            job["progress"] = progress
        if stage is not None:
            job["stage"] = stage
        job["updated"] = _now()
    logger.info("[%s] %s", job_id, message)
    _persist(job_id)


def workdir(job_id: str) -> Path:
    with _LOCK:
        job = _JOBS.get(job_id)
    path = Path(job["workdir"]) if job else config.JOBS_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _persist(job_id: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        payload = json.loads(json.dumps(job, default=str))
    path = config.JOBS_DIR / job_id / "job.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def run_in_background(job_id: str, fn: Callable[[str], Any]) -> None:
    """Run `fn(job_id)`; store its return value as the job result."""

    def _wrapper() -> None:
        update(job_id, status="running", stage="starting")
        try:
            result = fn(job_id)
            update(job_id, status="done", stage="done", progress=100,
                   result=result)
            log(job_id, "Finished")
        except Exception as exc:                        # noqa: BLE001
            tb = traceback.format_exc()
            logger.error("[%s] failed: %s\n%s", job_id, exc, tb)
            update(job_id, status="error", stage="error",
                   error=f"{exc.__class__.__name__}: {exc}")
            log(job_id, f"FAILED: {exc}")
            (workdir(job_id) / "traceback.txt").write_text(tb)

    _POOL.submit(_wrapper)
