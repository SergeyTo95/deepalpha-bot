from __future__ import annotations

import logging
import os
import signal
import threading
import time

from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_director_service as director
from services import velia_research_dataset_service as datasets
from services import velia_research_experiment_pipeline_service as experiment_pipeline
from services import velia_research_literature_service as literature
from services import velia_research_meta_analysis_service as meta_analysis
from services import velia_research_protocol_service as protocol
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports
from services import velia_research_systematic_review_service as systematic_review
from services import velia_research_living_service as living_research
from services import velia_research_living_reassessment_service as living_reassessment


logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    center.ensure_tables()
    claims.ensure_tables()
    literature.ensure_tables()
    meta_analysis.ensure_tables()
    systematic_review.ensure_tables()
    living_research.ensure_tables()
    living_reassessment.ensure_tables()
    reasoning.ensure_tables()
    datasets.ensure_tables()
    director.ensure_tables()
    experiment_pipeline.ensure_tables()
    protocol.ensure_tables()
    reports.ensure_tables()

    director_on = director.enabled()
    living_on = living_research.worker_enabled()
    reassessment_on = living_reassessment.worker_enabled()
    if not director_on and not living_on and not reassessment_on:
        logger.warning("VELIA_RESEARCH_WORKER_DISABLED")
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
            worked = False
            if director_on:
                run = director.claim_next(worker)
                if run:
                    worked = True
                    logger.info(
                        "VELIA_RESEARCH_RUN_STARTED run=%s mission=%s",
                        run["id"], run["mission_id"],
                    )
                    result = director.execute_claimed(run, worker)
                    logger.info(
                        "VELIA_RESEARCH_RUN_FINISHED run=%s status=%s reason=%s",
                        result["id"], result["status"], result.get("stop_reason", ""),
                    )
            if living_on:
                scan = living_research.run_due_once(worker)
                if scan:
                    worked = True
                    queued = living_reassessment.enqueue_for_scan(
                        int(living_research._watch_owner(scan["watch_id"]))
                        if scan.get("watch_id") else 0,
                        scan["id"],
                    ) if reassessment_on and scan.get("watch_id") else None
                    logger.info(
                        "VELIA_LIVING_RESEARCH_SCAN_FINISHED scan=%s mission=%s status=%s reassessment=%s",
                        scan["id"], scan["mission_id"], scan["material_status"],
                        queued["id"] if queued else "",
                    )
            if reassessment_on:
                reassessment_run = living_reassessment.claim_next(worker)
                if reassessment_run:
                    worked = True
                    reassessment_result = living_reassessment.execute_claimed(
                        reassessment_run, worker
                    )
                    logger.info(
                        "VELIA_LIVING_REASSESSMENT_FINISHED run=%s scan=%s status=%s",
                        reassessment_result["id"],
                        reassessment_result["scan_id"],
                        reassessment_result["status"],
                    )
            if not worked:
                stop.wait(poll_seconds)
        except Exception:
            logger.exception("VELIA_RESEARCH_WORKER_ITERATION_FAILED")
            stop.wait(poll_seconds)

    logger.info("VELIA_RESEARCH_DIRECTOR_WORKER_STOPPED worker=%s", worker)


if __name__ == "__main__":
    main()
