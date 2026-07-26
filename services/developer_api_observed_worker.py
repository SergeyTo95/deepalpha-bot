import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from services.developer_api_analysis_service import (
    api_analysis_max_attempts,
    api_analysis_poll_seconds,
    api_analysis_timeout_seconds,
    claim_next_quick_analysis_job,
    ensure_api_analysis_tables,
    process_claimed_quick_analysis_job,
    recover_stale_api_analysis_jobs,
)
from services.developer_api_observability_service import (
    ensure_api_observability_tables,
    touch_api_worker_heartbeat,
    worker_stale_seconds,
)

logger = logging.getLogger(__name__)


def _heartbeat_interval_seconds() -> float:
    try:
        value = float(str(os.getenv("API_WORKER_HEARTBEAT_SECONDS", "10") or "10"))
    except Exception:
        value = 10.0
    return max(2.0, min(value, 60.0))


def _heartbeat_metadata() -> Dict[str, object]:
    return {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "timeout_seconds": api_analysis_timeout_seconds(),
        "max_attempts": api_analysis_max_attempts(),
        "stale_after_seconds": worker_stale_seconds(),
    }


def _touch_safely(worker_id: str, *, status: str, current_job_id: Optional[str] = None) -> None:
    try:
        touch_api_worker_heartbeat(
            worker_id,
            status=status,
            current_job_id=current_job_id,
            metadata=_heartbeat_metadata(),
        )
    except Exception:
        logger.exception(
            "API_WORKER_HEARTBEAT_FAILED worker_id=%s status=%s job_id=%s",
            worker_id,
            status,
            current_job_id,
        )


@contextmanager
def running_job_heartbeat(worker_id: str, job_id: str) -> Iterator[None]:
    stop_event = threading.Event()
    interval = _heartbeat_interval_seconds()

    def heartbeat_loop() -> None:
        while not stop_event.wait(interval):
            _touch_safely(worker_id, status="running", current_job_id=job_id)

    _touch_safely(worker_id, status="running", current_job_id=job_id)
    thread = threading.Thread(
        target=heartbeat_loop,
        name=f"api-heartbeat-{job_id[-8:]}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=max(1.0, interval + 1.0))
        _touch_safely(worker_id, status="idle", current_job_id=None)


def run_observed_api_analysis_worker_forever(worker_id: Optional[str] = None) -> None:
    ensure_api_analysis_tables()
    ensure_api_observability_tables()
    identity = str(worker_id or f"{socket.gethostname()}:{os.getpid()}")[:120]
    logger.info(
        "API_ANALYSIS_WORKER_STARTED worker_id=%s timeout=%s max_attempts=%s heartbeat=%s",
        identity,
        api_analysis_timeout_seconds(),
        api_analysis_max_attempts(),
        _heartbeat_interval_seconds(),
    )
    _touch_safely(identity, status="starting")

    last_recovery = 0.0
    last_idle_heartbeat = 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_idle_heartbeat >= _heartbeat_interval_seconds():
                _touch_safely(identity, status="idle")
                last_idle_heartbeat = now

            if now - last_recovery >= 30:
                try:
                    recovered = recover_stale_api_analysis_jobs()
                    if recovered["retried"] or recovered["refunded"]:
                        logger.warning("API_ANALYSIS_STALE_RECOVERY %s", recovered)
                except Exception:
                    logger.exception("API_ANALYSIS_STALE_RECOVERY_FAILED")
                last_recovery = now

            try:
                job = claim_next_quick_analysis_job(identity)
            except Exception:
                logger.exception("API_ANALYSIS_JOB_CLAIM_FAILED")
                _touch_safely(identity, status="degraded")
                time.sleep(max(1.0, api_analysis_poll_seconds()))
                continue

            if not job:
                time.sleep(api_analysis_poll_seconds())
                continue

            job_id = str(job.get("job_id") or "")
            with running_job_heartbeat(identity, job_id):
                process_claimed_quick_analysis_job(job, identity)
    finally:
        _touch_safely(identity, status="stopped")
