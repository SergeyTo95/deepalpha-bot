from __future__ import annotations

import logging
import os
import signal
import threading
import time

from services import velia_research_center_service as center
from services import velia_research_director_service as director
from services import velia_research_experiment_pipeline_service as experiment_pipeline
from services import velia_research_literature_service as literature
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports


logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    center.ensure_tables()
    literature.ensure_tables()
    reasoning.ensure_tables()
    director.ensure_tables()
    experiment_pipeline.ensure_tables()
    reports.ensure_tables()

    if not director.enabled():
        logger.warning("VELIA_RESEARCH_DIRECTOR_WORKER_DISABLED")
        return

    try:
        poll_seconds = max(1.0, min(30.0, float(os.getenv("VELIA_RESEARCH_WORKER_POLL_SECONDS", "3"))))
    except ValueError:
        poll_seconds = 3.0

    stop = threading.Event()

    def request_stop(*_args) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    worker = director.worker_id()
    logger.info("VELIA_RESEARCH_DIRECTOR_WORKER_STARTED worker=%s", worker)

    while not stop.is_set():
        try:
            run = director.claim_next(worker)
            if not run:
                stop.wait(poll_seconds)
                continue
            logger.info(
                "VELIA_RESEARCH_RUN_STARTED run=%s mission=%s",
                run["id"], run["mission_id"],
            )
            result = director.execute_claimed(run, worker)
            logger.info(
                "VELIA_RESEARCH_RUN_FINISHED run=%s status=%s reason=%s",
                result["id"], result["status"], result.get("stop_reason", ""),
            )
        except Exception:
            logger.exception("VELIA_RESEARCH_DIRECTOR_WORKER_ITERATION_FAILED")
            stop.wait(poll_seconds)

    logger.info("VELIA_RESEARCH_DIRECTOR_WORKER_STOPPED worker=%s", worker)


if __name__ == "__main__":
    main()
