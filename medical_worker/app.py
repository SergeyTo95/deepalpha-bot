from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict

import pydicom
import SimpleITK as sitk
from aiohttp import web

from radar_adapter import RadarAdapter, RadarAdapterError, SCORE_SEMANTICS, UPSTREAM_COMMIT


logger = logging.getLogger("velia.medical.worker")
MAX_FILES = 5000
MAX_UNCOMPRESSED_BYTES = 3 * 1024 * 1024 * 1024
ALLOWED_FORMATS = {"dicom_zip", "nifti", "nifti_gz"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _json_response(payload: Dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, dumps=lambda v: json.dumps(v, ensure_ascii=False))


def _token() -> str:
    return str(os.getenv("VELIA_MEDICAL_WORKER_AUTH_TOKEN", "") or "").strip()


def _authorized(request: web.Request) -> bool:
    token = _token()
    return bool(token) and request.headers.get("Authorization") == "Bearer " + token


def _safe_case_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise web.HTTPBadRequest(text='{"error":"invalid_case_id"}', content_type="application/json")


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.jobs_root = root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / job_id

    def state_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "state.json"

    def write(self, job_id: str, state: Dict[str, Any]) -> None:
        folder = self.job_dir(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "state.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path(job_id))

    def read(self, job_id: str) -> Dict[str, Any]:
        path = self.state_path(job_id)
        if not path.exists():
            raise KeyError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def cleanup(self, ttl_seconds: int) -> None:
        now = time.time()
        for folder in self.jobs_root.iterdir():
            try:
                state = self.read(folder.name)
                updated = float(state.get("updated_epoch") or 0)
                if state.get("status") in {"completed", "failed"} and now - updated > ttl_seconds:
                    shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                continue


def _validate_nifti(path: Path) -> None:
    try:
        image = sitk.ReadImage(str(path))
    except Exception as exc:
        raise ValueError("invalid_nifti") from exc
    size = tuple(int(v) for v in image.GetSize())
    dimension = int(image.GetDimension())
    if dimension != 3 or len(size) != 3 or any(v < 8 or v > 4096 for v in size):
        raise ValueError("invalid_nifti_dimensions")
    if image.GetNumberOfComponentsPerPixel() != 1:
        raise ValueError("invalid_nifti_components")


def _zip_member_is_safe(info: zipfile.ZipInfo) -> bool:
    name = info.filename.replace("\\", "/")
    if not name or name.startswith("/") or name.startswith("../") or "/../" in name:
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    return not stat.S_ISLNK(mode)


def _extract_dicom_zip(src: Path, work: Path) -> Path:
    extract_root = work / "dicom"
    extract_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(src) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_FILES:
                raise ValueError("dicom_archive_file_count_rejected")
            total = sum(int(item.file_size or 0) for item in members)
            if total <= 0 or total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("dicom_archive_size_rejected")
            for info in members:
                if info.is_dir():
                    continue
                if not _zip_member_is_safe(info):
                    raise ValueError("dicom_archive_path_rejected")
                target = (extract_root / info.filename).resolve()
                if extract_root.resolve() not in target.parents:
                    raise ValueError("dicom_archive_path_rejected")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest, length=1024 * 1024)
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid_dicom_zip") from exc

    candidate_dirs = {path.parent for path in extract_root.rglob("*") if path.is_file()}
    series_candidates = []
    for folder in sorted(candidate_dirs):
        try:
            for series_id in sitk.ImageSeriesReader.GetGDCMSeriesIDs(str(folder)) or []:
                files = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(str(folder), series_id)
                if files:
                    series_candidates.append((folder, series_id, list(files)))
        except Exception:
            continue
    if len(series_candidates) != 1:
        raise ValueError("single_dicom_series_required")
    _, _, files = series_candidates[0]

    try:
        sample = pydicom.dcmread(files[0], stop_before_pixels=True, force=False)
    except Exception as exc:
        raise ValueError("invalid_dicom_series") from exc
    if str(getattr(sample, "Modality", "") or "").upper() != "CT":
        raise ValueError("dicom_modality_not_ct")

    reader = sitk.ImageSeriesReader()
    reader.SetFileNames(files)
    try:
        image = reader.Execute()
    except Exception as exc:
        raise ValueError("dicom_conversion_failed") from exc
    if image.GetDimension() != 3:
        raise ValueError("invalid_dicom_dimensions")

    nifti = work / "study.nii.gz"
    sitk.WriteImage(image, str(nifti), useCompression=True)
    _validate_nifti(nifti)
    # The NIfTI conversion intentionally discards DICOM patient metadata.
    shutil.rmtree(extract_root, ignore_errors=True)
    return nifti


def _prepare_input(upload_path: Path, upload_format: str, work: Path) -> Path:
    if upload_format == "dicom_zip":
        return _extract_dicom_zip(upload_path, work)
    target = work / ("study.nii.gz" if upload_format == "nifti_gz" else "study.nii")
    os.replace(upload_path, target)
    _validate_nifti(target)
    return target


async def create_app() -> web.Application:
    work_root = Path(os.getenv("VELIA_MEDICAL_WORK_DIR", "/var/lib/velia-medical")).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    store = JobStore(work_root)
    queue: asyncio.Queue[str] = asyncio.Queue()
    adapter = RadarAdapter(
        upstream_root=os.getenv("RADAR_UPSTREAM_ROOT", "/opt/damo-radar"),
        model_root=os.getenv("RADAR_MODEL_ROOT", "/models/radar"),
    )

    async def require_auth(request: web.Request) -> None:
        if not _authorized(request):
            raise web.HTTPUnauthorized(
                text='{"error":"unauthorized"}', content_type="application/json"
            )

    async def health(request: web.Request) -> web.Response:
        await require_auth(request)
        readiness = adapter.readiness()
        license_ack = _env_bool("VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK", False)
        return _json_response({
            "ok": True,
            "service": "velia-medical-worker",
            "provider": "radar",
            "upstream_commit": UPSTREAM_COMMIT,
            "weights_license": "CC-BY-NC-SA-4.0",
            "commercial_use_allowed_by_public_weights": False,
            "noncommercial_license_acknowledged": license_ack,
            "model_files_ready": bool(readiness["ready"]),
            "ready_for_inference": bool(readiness["ready"] and license_ack),
            "score_semantics": SCORE_SEMANTICS,
            "raw_input_retained_after_job": False,
            "arbitrary_code_execution": False,
        })

    async def create_job(request: web.Request) -> web.Response:
        await require_auth(request)
        if not _env_bool("VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK", False):
            return _json_response({"error": "radar_weights_license_not_acknowledged"}, 503)
        if not adapter.readiness()["ready"]:
            return _json_response({"error": "radar_model_files_missing"}, 503)

        case_id = _safe_case_id(request.headers.get("X-Velia-Medical-Case", ""))
        upload_format = str(request.headers.get("X-Velia-Medical-Format", "") or "").strip().lower()
        if upload_format not in ALLOWED_FORMATS:
            return _json_response({"error": "medical_upload_format_not_supported"}, 415)

        declared_bytes = request.headers.get("X-Velia-Medical-Bytes")
        try:
            declared_size = int(declared_bytes or "0")
        except ValueError:
            return _json_response({"error": "invalid_medical_upload_size"}, 400)
        max_bytes = _env_int(
            "VELIA_MEDICAL_MAX_UPLOAD_BYTES",
            1024 * 1024 * 1024,
            16 * 1024 * 1024,
            2 * 1024 * 1024 * 1024,
        )
        if declared_size <= 0 or declared_size > max_bytes:
            return _json_response({"error": "medical_upload_size_rejected"}, 413)

        job_id = str(uuid.uuid4())
        folder = store.job_dir(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        upload_path = folder / "upload.bin"
        digest = hashlib.sha256()
        received = 0
        try:
            with upload_path.open("wb") as handle:
                async for chunk in request.content.iter_chunked(1024 * 1024):
                    received += len(chunk)
                    if received > max_bytes or received > declared_size:
                        raise ValueError("medical_upload_size_rejected")
                    digest.update(chunk)
                    handle.write(chunk)
            if received != declared_size:
                raise ValueError("medical_upload_size_mismatch")
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            return _json_response({"error": "medical_upload_failed"}, 400)

        state = {
            "job_id": job_id,
            "case_id": case_id,
            "status": "queued",
            "upload_format": upload_format,
            "input_sha256": digest.hexdigest(),
            "result": None,
            "error": None,
            "updated_epoch": time.time(),
        }
        store.write(job_id, state)
        await queue.put(job_id)
        return _json_response({
            "ok": True,
            "job_id": job_id,
            "status": "queued",
            "input_sha256": state["input_sha256"],
        }, 202)

    async def get_job(request: web.Request) -> web.Response:
        await require_auth(request)
        job_id = request.match_info["job_id"]
        try:
            uuid.UUID(job_id)
            state = store.read(job_id)
        except (ValueError, KeyError):
            return _json_response({"error": "medical_job_not_found"}, 404)
        expected_case = str(request.headers.get("X-Velia-Medical-Case") or "")
        if not expected_case or state.get("case_id") != expected_case:
            return _json_response({"error": "medical_job_not_found"}, 404)
        return _json_response({
            "ok": True,
            "job_id": job_id,
            "status": state["status"],
            "input_sha256": state["input_sha256"],
            "result": state.get("result"),
            "error": state.get("error"),
        })

    async def worker_loop() -> None:
        while True:
            job_id = await queue.get()
            folder = store.job_dir(job_id)
            try:
                state = store.read(job_id)
                state["status"] = "running"
                state["updated_epoch"] = time.time()
                store.write(job_id, state)
                prepared = next(
                    (candidate for candidate in (folder / "study.nii.gz", folder / "study.nii") if candidate.exists()),
                    None,
                )
                if prepared is None:
                    prepared = await asyncio.to_thread(
                        _prepare_input,
                        folder / "upload.bin",
                        state["upload_format"],
                        folder,
                    )
                else:
                    await asyncio.to_thread(_validate_nifti, prepared)
                result = await asyncio.to_thread(
                    adapter.infer,
                    str(prepared),
                    str(folder),
                    job_id,
                )
                state["status"] = "completed"
                state["result"] = result
                state["error"] = None
            except (ValueError, RadarAdapterError) as exc:
                state = store.read(job_id)
                state["status"] = "failed"
                state["error"] = str(exc)[:160] or "medical_inference_failed"
                state["result"] = None
            except Exception:
                logger.exception("MEDICAL_JOB_FAILED job=%s", job_id)
                state = store.read(job_id)
                state["status"] = "failed"
                state["error"] = "medical_inference_failed"
                state["result"] = None
            finally:
                # Remove all raw/derived images. Keep only state.json containing
                # structured scores so Railway can reconcile the job.
                for path in folder.iterdir():
                    if path.name != "state.json":
                        if path.is_dir():
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            try:
                                path.unlink()
                            except FileNotFoundError:
                                pass
                state["updated_epoch"] = time.time()
                store.write(job_id, state)
                queue.task_done()

    async def janitor_loop() -> None:
        ttl = _env_int("VELIA_MEDICAL_RESULT_TTL_SECONDS", 86400, 3600, 604800)
        while True:
            await asyncio.sleep(900)
            await asyncio.to_thread(store.cleanup, ttl)

    async def on_startup(_app: web.Application) -> None:
        # Recover queued/running jobs only if their raw upload is still present.
        for folder in store.jobs_root.iterdir():
            try:
                state = store.read(folder.name)
                recoverable_input = any(
                    candidate.exists()
                    for candidate in (folder / "upload.bin", folder / "study.nii", folder / "study.nii.gz")
                )
                if state.get("status") in {"queued", "running"} and recoverable_input:
                    state["status"] = "queued"
                    state["updated_epoch"] = time.time()
                    store.write(folder.name, state)
                    await queue.put(folder.name)
            except Exception:
                continue
        _app["medical_worker_loop"] = asyncio.create_task(worker_loop())
        _app["medical_janitor_loop"] = asyncio.create_task(janitor_loop())

    async def on_cleanup(_app: web.Application) -> None:
        for key in ("medical_worker_loop", "medical_janitor_loop"):
            task = _app.get(key)
            if task:
                task.cancel()

    app = web.Application(client_max_size=_env_int(
        "VELIA_MEDICAL_MAX_UPLOAD_BYTES",
        1024 * 1024 * 1024,
        16 * 1024 * 1024,
        2 * 1024 * 1024 * 1024,
    ))
    app.router.add_get("/health", health)
    app.router.add_post("/v1/jobs", create_job)
    app.router.add_get("/v1/jobs/{job_id}", get_job)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = int(os.getenv("PORT", "8088"))
    web.run_app(create_app(), host="0.0.0.0", port=port, access_log=None)


if __name__ == "__main__":
    main()
