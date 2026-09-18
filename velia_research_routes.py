import asyncio
import json
import logging

from services import velia_project_service as projects
from services import velia_research_center_service as research
from services import velia_research_claim_service as claims
from services import velia_research_compute_service as compute
from services import velia_research_dataset_service as datasets
from services import velia_research_experiment_pipeline_service as experiment_pipeline
from services import velia_research_protocol_service as protocol
from services import velia_research_director_service as director
from services import velia_research_literature_service as literature
from services import velia_research_meta_analysis_service as meta_analysis
from services import velia_research_reasoning_service as reasoning
from services import velia_research_report_service as reports
from services import velia_research_systematic_review_service as systematic_review
from services import velia_research_living_service as living_research
from velia_mobile_routes import _json_response, _mobile_api_available, _require_mobile_auth


MAX_BODY = 64 * 1024
MAX_DATASET_BODY = 512 * 1024


async def _body(request, max_body=MAX_BODY):
    raw = bytearray()
    async for chunk in request.content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) > max_body:
            raise projects.ProjectError("request_too_large", 413)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise projects.ProjectError("invalid_json")
    if not isinstance(data, dict):
        raise projects.ProjectError("invalid_json")
    return data


def setup_velia_research_routes(app):
    async def startup(_app):
        if not _mobile_api_available():
            return
        try:
            await asyncio.to_thread(research.ensure_tables)
            await asyncio.to_thread(claims.ensure_tables)
            await asyncio.to_thread(literature.ensure_tables)
            await asyncio.to_thread(meta_analysis.ensure_tables)
            await asyncio.to_thread(systematic_review.ensure_tables)
            await asyncio.to_thread(living_research.ensure_tables)
            await asyncio.to_thread(reasoning.ensure_tables)
            await asyncio.to_thread(director.ensure_tables)
            await asyncio.to_thread(reports.ensure_tables)
            await asyncio.to_thread(compute.ensure_tables)
            await asyncio.to_thread(datasets.ensure_tables)
            await asyncio.to_thread(experiment_pipeline.ensure_tables)
            await asyncio.to_thread(protocol.ensure_tables)
        except Exception:
            logging.getLogger(__name__).exception("VELIA_RESEARCH_STORAGE_UNAVAILABLE")

    app.on_startup.append(startup)

    def guarded(handler, *, storage=True):
        async def wrapped(request):
            if not _mobile_api_available():
                return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
            auth = await asyncio.to_thread(_require_mobile_auth, request)
            if not auth:
                return _json_response({"ok": False, "error": "unauthorized"}, status=401)
            expected_account = request.headers.get("X-Velia-Account")
            if expected_account is not None and expected_account != str(auth["user_id"]):
                return _json_response({"ok": False, "error": "account_changed"}, status=409)
            if storage and not projects.ready():
                return _json_response({"ok": False, "error": "research_unavailable"}, status=503)
            try:
                return await handler(request, int(auth["user_id"]))
            except projects.ProjectError as exc:
                return _json_response({"ok": False, "error": exc.code}, status=exc.status)
            except (TypeError, ValueError):
                return _json_response({"ok": False, "error": "invalid_request"}, status=400)
        return wrapped

    async def get_status(request, uid):
        del request, uid
        state = research.status()
        state["literature"] = literature.status()
        state["reasoning"] = reasoning.status()
        state["director"] = director.status()
        state["reports"] = reports.status()
        state["compute"] = compute.status()
        state["datasets"] = datasets.status()
        state["experiment_pipeline"] = experiment_pipeline.status()
        state["protocol_officer"] = protocol.status()
        state["claim_ledger"] = claims.status()
        state["meta_analysis"] = meta_analysis.status()
        state["systematic_review"] = systematic_review.status()
        state["living_research"] = living_research.status()
        return _json_response({"ok": True, "research": state})

    async def mission_list(request, uid):
        data = await asyncio.to_thread(research.list_missions, uid, int(request.query.get("offset", 0)))
        return _json_response({"ok": True, **data})

    async def mission_create(request, uid):
        data = await _body(request)
        mission = await asyncio.to_thread(
            research.create_mission, uid, data, request.headers.get("Idempotency-Key")
        )
        return _json_response({"ok": True, "mission": mission}, status=201)

    async def mission_get(request, uid):
        mission = await asyncio.to_thread(research.get_mission, uid, request.match_info["mission_id"])
        return _json_response({"ok": True, "mission": mission})

    async def mission_events(request, uid):
        events = await asyncio.to_thread(
            research.list_events, uid, request.match_info["mission_id"], int(request.query.get("after", 0))
        )
        return _json_response({"ok": True, "events": events})

    async def hypothesis_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            research.add_hypothesis,
            uid,
            request.match_info["mission_id"],
            data.get("title", ""),
            data.get("rationale", ""),
        )
        return _json_response({"ok": True, "hypothesis": item}, status=201)

    async def experiment_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            research.plan_experiment,
            uid,
            request.match_info["mission_id"],
            data.get("method"),
            data.get("hypothesis_id"),
        )
        return _json_response({"ok": True, "experiment": item}, status=201)

    async def literature_search(request, uid):
        data = await _body(request)
        result = await asyncio.to_thread(
            literature.collect,
            uid,
            request.match_info["mission_id"],
            data.get("query", ""),
            data.get("max_results", 12),
        )
        return _json_response({"ok": True, "literature": result})

    async def literature_sources(request, uid):
        result = await asyncio.to_thread(
            literature.list_sources,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def synthesis_create(request, uid):
        data = await _body(request)
        result = await asyncio.to_thread(
            reasoning.synthesize,
            uid,
            request.match_info["mission_id"],
            data.get("max_sources", 12),
        )
        return _json_response({"ok": True, "synthesis": result})

    async def synthesis_list(request, uid):
        result = await asyncio.to_thread(
            reasoning.list_syntheses,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def autonomy_run_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            director.enqueue,
            uid,
            request.match_info["mission_id"],
            data.get("max_iterations", 2),
        )
        return _json_response({"ok": True, "run": item}, status=202)

    async def autonomy_runs_list(request, uid):
        result = await asyncio.to_thread(
            director.list_runs,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def autonomy_run_get(request, uid):
        item = await asyncio.to_thread(
            director.get_run,
            uid,
            request.match_info["run_id"],
        )
        return _json_response({"ok": True, "run": item})

    async def autonomy_run_cancel(request, uid):
        item = await asyncio.to_thread(
            director.cancel_run,
            uid,
            request.match_info["run_id"],
        )
        return _json_response({"ok": True, "run": item})

    async def report_create(request, uid):
        item = await asyncio.to_thread(
            reports.build_report,
            uid,
            request.match_info["mission_id"],
        )
        return _json_response({"ok": True, "report": item}, status=201)

    async def reports_list(request, uid):
        result = await asyncio.to_thread(
            reports.list_reports,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def report_get(request, uid):
        item = await asyncio.to_thread(
            reports.get_report,
            uid,
            request.match_info["report_id"],
        )
        return _json_response({"ok": True, "report": item})

    async def compute_execute(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            compute.execute,
            uid,
            request.match_info["experiment_id"],
            data,
            request.headers.get("Idempotency-Key"),
        )
        return _json_response({"ok": True, "compute_run": item}, status=201)

    async def compute_runs_list(request, uid):
        result = await asyncio.to_thread(
            compute.list_runs,
            uid,
            request.match_info["experiment_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def compute_run_get(request, uid):
        item = await asyncio.to_thread(
            compute.get_run,
            uid,
            request.match_info["run_id"],
        )
        return _json_response({"ok": True, "compute_run": item})

    async def dataset_create(request, uid):
        data = await _body(request, MAX_DATASET_BODY)
        item = await asyncio.to_thread(
            datasets.create,
            uid,
            request.match_info["mission_id"],
            data,
        )
        return _json_response({"ok": True, "dataset": item}, status=201)

    async def datasets_list(request, uid):
        result = await asyncio.to_thread(
            datasets.list_datasets,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def dataset_get(request, uid):
        if str(request.query.get("include_rows", "")).lower() in {"1", "true", "yes"}:
            raise projects.ProjectError("research_dataset_rows_not_exposed", 403)
        item = await asyncio.to_thread(
            datasets.get,
            uid,
            request.match_info["dataset_id"],
            include_rows=False,
        )
        return _json_response({"ok": True, "dataset": item})

    async def claim_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            claims.create_claim,
            uid,
            request.match_info["mission_id"],
            data,
        )
        return _json_response({"ok": True, "claim": item}, status=201)

    async def claims_list(request, uid):
        result = await asyncio.to_thread(
            claims.list_claims,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def claim_get(request, uid):
        item = await asyncio.to_thread(
            claims.get_claim,
            uid,
            request.match_info["claim_id"],
        )
        return _json_response({"ok": True, "claim": item})

    async def claim_evaluate(request, uid):
        item = await asyncio.to_thread(
            claims.refresh_claim,
            uid,
            request.match_info["claim_id"],
        )
        return _json_response({"ok": True, "snapshot": item})

    async def mission_claim_ledger(request, uid):
        result = await asyncio.to_thread(
            claims.mission_ledger,
            uid,
            request.match_info["mission_id"],
        )
        return _json_response({"ok": True, **result})

    async def meta_study_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            meta_analysis.register_study,
            uid,
            request.match_info["claim_id"],
            data,
        )
        return _json_response({"ok": True, "study": item}, status=201)

    async def meta_studies_list(request, uid):
        result = await asyncio.to_thread(
            meta_analysis.list_studies,
            uid,
            request.match_info["claim_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def meta_analyze(request, uid):
        item = await asyncio.to_thread(
            meta_analysis.analyze_claim,
            uid,
            request.match_info["claim_id"],
        )
        return _json_response({"ok": True, "meta_analysis": item}, status=201)

    async def meta_snapshots_list(request, uid):
        result = await asyncio.to_thread(
            meta_analysis.list_snapshots,
            uid,
            request.match_info["claim_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def meta_snapshot_get(request, uid):
        item = await asyncio.to_thread(
            meta_analysis.get_snapshot,
            uid,
            request.match_info["snapshot_id"],
        )
        return _json_response({"ok": True, "meta_analysis": item})

    async def mission_meta_evidence(request, uid):
        result = await asyncio.to_thread(
            meta_analysis.mission_meta_evidence,
            uid,
            request.match_info["mission_id"],
        )
        return _json_response({"ok": True, **result})

    async def systematic_review_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            systematic_review.create_protocol,
            uid,
            request.match_info["mission_id"],
            data,
        )
        return _json_response({"ok": True, "review": item}, status=201)

    async def systematic_reviews_list(request, uid):
        result = await asyncio.to_thread(
            systematic_review.list_protocols,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def systematic_review_get(request, uid):
        item = await asyncio.to_thread(
            systematic_review.get_protocol,
            uid,
            request.match_info["review_id"],
        )
        return _json_response({"ok": True, "review": item})

    async def systematic_review_search(request, uid):
        item = await asyncio.to_thread(
            systematic_review.execute_search,
            uid,
            request.match_info["review_id"],
        )
        return _json_response({"ok": True, **item})

    async def systematic_review_screen(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            systematic_review.screen_source,
            uid,
            request.match_info["review_id"],
            request.match_info["source_id"],
            data,
        )
        return _json_response({"ok": True, "screening": item}, status=201)

    async def systematic_review_screening_history(request, uid):
        source_id = request.query.get("source_id")
        result = await asyncio.to_thread(
            systematic_review.screening_history,
            uid,
            request.match_info["review_id"],
            source_id,
        )
        return _json_response({"ok": True, **result})

    async def systematic_review_flow(request, uid):
        result = await asyncio.to_thread(
            systematic_review.prisma_flow,
            uid,
            request.match_info["review_id"],
        )
        return _json_response({"ok": True, "flow": result})

    async def systematic_review_graph(request, uid):
        result = await asyncio.to_thread(
            systematic_review.build_evidence_graph,
            uid,
            request.match_info["review_id"],
        )
        return _json_response({"ok": True, "evidence_graph": result})

    async def living_watch_configure(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            living_research.configure_watch,
            uid,
            request.match_info["review_id"],
            data,
        )
        return _json_response({"ok": True, "watch": item})

    async def living_watch_get(request, uid):
        item = await asyncio.to_thread(
            living_research.get_watch,
            uid,
            request.match_info["review_id"],
        )
        return _json_response({"ok": True, "watch": item})

    async def living_scan_create(request, uid):
        item = await asyncio.to_thread(
            living_research.scan_review,
            uid,
            request.match_info["review_id"],
            trigger_kind="manual",
        )
        return _json_response({"ok": True, "living_scan": item}, status=201)

    async def living_scans_list(request, uid):
        result = await asyncio.to_thread(
            living_research.list_scans,
            uid,
            request.match_info["review_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def living_scan_get(request, uid):
        item = await asyncio.to_thread(
            living_research.get_scan,
            uid,
            request.match_info["scan_id"],
        )
        return _json_response({"ok": True, "living_scan": item})

    async def protocol_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            protocol.create_locked,
            uid,
            request.match_info["mission_id"],
            data,
        )
        return _json_response({"ok": True, "protocol": item}, status=201)

    async def protocols_list(request, uid):
        result = await asyncio.to_thread(
            protocol.list_protocols,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def protocol_get(request, uid):
        item = await asyncio.to_thread(
            protocol.get,
            uid,
            request.match_info["protocol_id"],
        )
        return _json_response({"ok": True, "protocol": item})

    async def protocol_correct_pvalues(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            protocol.correct_p_values,
            uid,
            request.match_info["protocol_id"],
            data.get("p_values"),
        )
        return _json_response({"ok": True, "correction": item})

    async def experiment_plan_create(request, uid):
        data = await _body(request)
        item = await asyncio.to_thread(
            experiment_pipeline.plan,
            uid,
            request.match_info["mission_id"],
            data,
        )
        return _json_response({"ok": True, "experiment": item}, status=201)

    async def experiment_pipeline_run(request, uid):
        data = await _body(request)
        result = await asyncio.to_thread(
            experiment_pipeline.run_ready,
            uid,
            request.match_info["mission_id"],
            data.get("max_experiments", 1),
        )
        return _json_response({"ok": True, **result})

    async def experiment_reviews_list(request, uid):
        result = await asyncio.to_thread(
            experiment_pipeline.list_reviews,
            uid,
            request.match_info["mission_id"],
            int(request.query.get("offset", 0)),
        )
        return _json_response({"ok": True, **result})

    async def mission_cancel(request, uid):
        mission = await asyncio.to_thread(research.cancel_mission, uid, request.match_info["mission_id"])
        return _json_response({"ok": True, "mission": mission})

    prefix = "/mobile-api/v1/research"
    app.router.add_get(prefix + "/status", guarded(get_status, storage=False))
    app.router.add_get(prefix + "/missions", guarded(mission_list))
    app.router.add_post(prefix + "/missions", guarded(mission_create))
    app.router.add_get(prefix + "/missions/{mission_id}", guarded(mission_get))
    app.router.add_get(prefix + "/missions/{mission_id}/events", guarded(mission_events))
    app.router.add_post(prefix + "/missions/{mission_id}/literature", guarded(literature_search))
    app.router.add_get(prefix + "/missions/{mission_id}/sources", guarded(literature_sources))
    app.router.add_post(prefix + "/missions/{mission_id}/synthesize", guarded(synthesis_create))
    app.router.add_get(prefix + "/missions/{mission_id}/syntheses", guarded(synthesis_list))
    app.router.add_post(prefix + "/missions/{mission_id}/runs", guarded(autonomy_run_create))
    app.router.add_get(prefix + "/missions/{mission_id}/runs", guarded(autonomy_runs_list))
    app.router.add_get(prefix + "/runs/{run_id}", guarded(autonomy_run_get))
    app.router.add_post(prefix + "/runs/{run_id}/cancel", guarded(autonomy_run_cancel))
    app.router.add_post(prefix + "/missions/{mission_id}/reports", guarded(report_create))
    app.router.add_get(prefix + "/missions/{mission_id}/reports", guarded(reports_list))
    app.router.add_get(prefix + "/reports/{report_id}", guarded(report_get))
    app.router.add_post(prefix + "/missions/{mission_id}/hypotheses", guarded(hypothesis_create))
    app.router.add_post(prefix + "/missions/{mission_id}/experiments", guarded(experiment_create))
    app.router.add_post(prefix + "/missions/{mission_id}/datasets", guarded(dataset_create))
    app.router.add_get(prefix + "/missions/{mission_id}/datasets", guarded(datasets_list))
    app.router.add_get(prefix + "/datasets/{dataset_id}", guarded(dataset_get))
    app.router.add_post(prefix + "/missions/{mission_id}/claims", guarded(claim_create))
    app.router.add_get(prefix + "/missions/{mission_id}/claims", guarded(claims_list))
    app.router.add_get(prefix + "/claims/{claim_id}", guarded(claim_get))
    app.router.add_post(prefix + "/claims/{claim_id}/evaluate", guarded(claim_evaluate))
    app.router.add_get(prefix + "/missions/{mission_id}/claim-ledger", guarded(mission_claim_ledger))
    app.router.add_post(prefix + "/claims/{claim_id}/meta-studies", guarded(meta_study_create))
    app.router.add_get(prefix + "/claims/{claim_id}/meta-studies", guarded(meta_studies_list))
    app.router.add_post(prefix + "/claims/{claim_id}/meta-analysis", guarded(meta_analyze))
    app.router.add_get(prefix + "/claims/{claim_id}/meta-analyses", guarded(meta_snapshots_list))
    app.router.add_get(prefix + "/meta-analyses/{snapshot_id}", guarded(meta_snapshot_get))
    app.router.add_get(prefix + "/missions/{mission_id}/meta-evidence", guarded(mission_meta_evidence))
    app.router.add_post(prefix + "/missions/{mission_id}/systematic-reviews", guarded(systematic_review_create))
    app.router.add_get(prefix + "/missions/{mission_id}/systematic-reviews", guarded(systematic_reviews_list))
    app.router.add_get(prefix + "/systematic-reviews/{review_id}", guarded(systematic_review_get))
    app.router.add_post(prefix + "/systematic-reviews/{review_id}/search", guarded(systematic_review_search))
    app.router.add_post(prefix + "/systematic-reviews/{review_id}/sources/{source_id}/screen", guarded(systematic_review_screen))
    app.router.add_get(prefix + "/systematic-reviews/{review_id}/screening", guarded(systematic_review_screening_history))
    app.router.add_get(prefix + "/systematic-reviews/{review_id}/flow", guarded(systematic_review_flow))
    app.router.add_post(prefix + "/systematic-reviews/{review_id}/evidence-graph", guarded(systematic_review_graph))
    app.router.add_post(prefix + "/systematic-reviews/{review_id}/living-watch", guarded(living_watch_configure))
    app.router.add_get(prefix + "/systematic-reviews/{review_id}/living-watch", guarded(living_watch_get))
    app.router.add_post(prefix + "/systematic-reviews/{review_id}/living-scan", guarded(living_scan_create))
    app.router.add_get(prefix + "/systematic-reviews/{review_id}/living-scans", guarded(living_scans_list))
    app.router.add_get(prefix + "/living-scans/{scan_id}", guarded(living_scan_get))
    app.router.add_post(prefix + "/missions/{mission_id}/protocols", guarded(protocol_create))
    app.router.add_get(prefix + "/missions/{mission_id}/protocols", guarded(protocols_list))
    app.router.add_get(prefix + "/protocols/{protocol_id}", guarded(protocol_get))
    app.router.add_post(prefix + "/protocols/{protocol_id}/correct-pvalues", guarded(protocol_correct_pvalues))
    app.router.add_post(prefix + "/missions/{mission_id}/experiment-plans", guarded(experiment_plan_create))
    app.router.add_post(prefix + "/missions/{mission_id}/experiment-pipeline/run", guarded(experiment_pipeline_run))
    app.router.add_get(prefix + "/missions/{mission_id}/experiment-reviews", guarded(experiment_reviews_list))
    app.router.add_post(prefix + "/experiments/{experiment_id}/compute", guarded(compute_execute))
    app.router.add_get(prefix + "/experiments/{experiment_id}/compute-runs", guarded(compute_runs_list))
    app.router.add_get(prefix + "/compute-runs/{run_id}", guarded(compute_run_get))
    app.router.add_post(prefix + "/missions/{mission_id}/cancel", guarded(mission_cancel))
