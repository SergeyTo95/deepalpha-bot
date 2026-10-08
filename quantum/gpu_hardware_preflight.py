#!/usr/bin/env python3
"""Fail-fast hardware validation for the first VELIA Quantum RCO run."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def _gpu_inventory() -> list[dict]:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        raise RuntimeError(f"nvidia-smi failed: {exc.__class__.__name__}") from exc

    gpus = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        parts = [part.strip() for part in raw.split(",", 2)]
        if len(parts) != 3:
            raise RuntimeError(f"unexpected nvidia-smi row: {raw!r}")
        index, name, memory_mib = parts
        gpus.append(
            {
                "index": int(index),
                "name": name,
                "memory_mib": int(memory_mib),
                "memory_gb_decimal": round(int(memory_mib) * 1024 * 1024 / 1e9, 2),
            }
        )
    return gpus


def _system_ram_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 0.0
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            kib = int(line.split()[1])
            return kib * 1024 / 1e9
    return 0.0


def evaluate(spec: dict, profile: str, work_dir: Path, check_torch: bool) -> dict:
    errors = []
    warnings = []
    required = spec["hardware"][profile]

    try:
        gpus = _gpu_inventory()
    except RuntimeError as exc:
        gpus = []
        errors.append(str(exc))

    required_count = int(required["gpu_count"])
    required_each = float(required["gpu_memory_gb_each"])
    # NVIDIA reports MiB while rental listings usually use nominal GB. A
    # nominal 80 GB accelerator may report slightly below 80 decimal GB.
    per_gpu_floor = required_each * 0.95

    if len(gpus) < required_count:
        errors.append(f"GPU count {len(gpus)} < required {required_count}")
    for gpu in gpus[:required_count]:
        if gpu["memory_gb_decimal"] + 1e-9 < per_gpu_floor:
            errors.append(
                f"GPU {gpu['index']} VRAM {gpu['memory_gb_decimal']:.2f} GB "
                f"< safe floor {per_gpu_floor:.2f} GB"
            )

    ram_gb = _system_ram_gb()
    required_ram = float(required["system_ram_gb_min"])
    if ram_gb + 1e-9 < required_ram * 0.95:
        errors.append(
            f"system RAM {ram_gb:.1f} GB < safe floor {required_ram * 0.95:.1f} GB"
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(work_dir)
    free_gb = disk.free / 1e9
    required_disk = float(required["local_nvme_gb_min"])
    if free_gb + 1e-9 < required_disk * 0.95:
        errors.append(
            f"free disk {free_gb:.1f} GB < safe floor {required_disk * 0.95:.1f} GB"
        )

    torch_report = None
    if check_torch:
        try:
            import torch
            torch_report = {
                "version": torch.__version__,
                "cuda_available": bool(torch.cuda.is_available()),
                "cuda_device_count": int(torch.cuda.device_count()),
            }
            if not torch.cuda.is_available():
                errors.append("torch.cuda.is_available() is false")
            elif torch.cuda.device_count() < required_count:
                errors.append(
                    f"PyTorch sees {torch.cuda.device_count()} GPUs; "
                    f"need {required_count}"
                )
        except Exception as exc:
            errors.append(f"PyTorch CUDA check failed: {exc.__class__.__name__}")

    if gpus:
        total_vram = sum(gpu["memory_gb_decimal"] for gpu in gpus)
        if total_vram < required_count * required_each * 0.95:
            errors.append(
                f"total visible VRAM {total_vram:.1f} GB is below safe preferred capacity"
            )
    else:
        total_vram = 0.0

    return {
        "ok": not errors,
        "profile": profile,
        "planning_target_not_measured_peak": True,
        "gpus": gpus,
        "gpu_count": len(gpus),
        "total_vram_gb_decimal": round(total_vram, 2),
        "system_ram_gb_decimal": round(ram_gb, 2),
        "disk_free_gb_decimal": round(free_gb, 2),
        "work_dir": str(work_dir.resolve()),
        "torch": torch_report,
        "requirements": required,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path(__file__).with_name("gpu_job_spec.json"),
    )
    parser.add_argument(
        "--profile",
        choices=("smoke_search", "faithful_bf16_search_preferred"),
        default="smoke_search",
    )
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--check-torch", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    report = evaluate(spec, args.profile, args.work_dir, args.check_torch)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
