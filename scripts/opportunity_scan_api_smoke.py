#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Optional


def request_json(
    method: str,
    url: str,
    *,
    api_key: str,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    body = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "DeepAlpha-Opportunity-Smoke/1.0",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
            result["_http_status"] = int(response.status)
            return result
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            result = json.loads(text)
        except Exception:
            result = {"ok": False, "error": text[:500] or str(exc.reason)}
        result["_http_status"] = int(exc.code)
        return result


def balance(payload: Dict[str, Any]) -> int:
    client = payload.get("client") if isinstance(payload.get("client"), dict) else {}
    return int(client.get("credit_balance") or 0)


def validate_terminal(payload: Dict[str, Any]) -> Dict[str, int]:
    status = str(payload.get("status") or "")
    if status not in {"success", "error"}:
        raise AssertionError(f"terminal status required, got {status!r}")
    credits = payload.get("credits") if isinstance(payload.get("credits"), dict) else {}
    reserved = int(credits.get("reserved") or 0)
    charged = int(credits.get("charged") or 0)
    refunded = int(credits.get("refunded") or 0)
    if reserved != 1:
        raise AssertionError(f"expected default reservation of 1 credit, got {reserved}")
    if status == "success":
        if charged != 1 or refunded != 0 or credits.get("reservation_status") != "charged":
            raise AssertionError(f"invalid success settlement: {credits}")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if result.get("scan_type") != "opportunity_scan":
            raise AssertionError("missing opportunity_scan result")
        if int(result.get("provider_calls") or 0) != 0 or result.get("paid_ai_used") is not False:
            raise AssertionError("Opportunity Scan must have zero provider calls")
    else:
        if charged != 0 or refunded != 1 or credits.get("reservation_status") != "refunded":
            raise AssertionError(f"invalid error settlement: {credits}")
    return {"reserved": reserved, "charged": charged, "refunded": refunded}


def run(args: argparse.Namespace) -> Dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    account_before = request_json("GET", f"{base_url}/api/v1/account", api_key=args.api_key)
    if not account_before.get("ok"):
        raise RuntimeError(f"account check failed: {account_before}")
    before = balance(account_before)

    idem = args.idempotency_key or f"opportunity-smoke:{uuid.uuid4().hex}"
    scan_payload = {
        "category": args.category,
        "language": args.language,
        "scan_limit": args.scan_limit,
        "result_limit": args.result_limit,
        "min_score": args.min_score,
        "tiers": ["DEEP_ANALYSIS_CANDIDATE", "WATCH_CANDIDATE"],
    }
    submitted = request_json(
        "POST",
        f"{base_url}/api/v1/opportunity-scans",
        api_key=args.api_key,
        idempotency_key=idem,
        payload=scan_payload,
    )
    if not submitted.get("ok"):
        raise RuntimeError(f"scan submission failed: {submitted}")
    job_id = str(submitted.get("job_id") or "")
    if not job_id.startswith("job_"):
        raise RuntimeError(f"invalid job id: {job_id!r}")

    deadline = time.monotonic() + args.wait_seconds
    terminal: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        current = request_json(
            "GET",
            f"{base_url}/api/v1/opportunity-scans/{job_id}",
            api_key=args.api_key,
        )
        print(json.dumps({
            "job_id": job_id,
            "status": current.get("status"),
            "progress": current.get("progress"),
            "credits": current.get("credits"),
        }, ensure_ascii=False))
        if current.get("status") in {"success", "error"}:
            terminal = current
            break
        time.sleep(args.poll_seconds)
    if terminal is None:
        raise TimeoutError(f"job {job_id} did not finish within {args.wait_seconds}s")
    settlement = validate_terminal(terminal)

    replay = request_json(
        "POST",
        f"{base_url}/api/v1/opportunity-scans",
        api_key=args.api_key,
        idempotency_key=idem,
        payload=scan_payload,
    )
    if not replay.get("ok") or replay.get("job_id") != job_id or replay.get("idempotent") is not True:
        raise AssertionError(f"idempotent replay failed: {replay}")

    account_after = request_json("GET", f"{base_url}/api/v1/account", api_key=args.api_key)
    if not account_after.get("ok"):
        raise RuntimeError(f"final account check failed: {account_after}")
    after = balance(account_after)
    expected = before - settlement["charged"]
    if settlement["refunded"]:
        expected = before
    matches = after == expected
    if args.strict_balance and not matches:
        raise AssertionError(
            f"balance mismatch before={before} after={after} expected={expected}; use a dedicated smoke project"
        )

    return {
        "ok": True,
        "job_id": job_id,
        "idempotency_key": idem,
        "status": terminal.get("status"),
        "settlement": settlement,
        "candidate_count": (
            terminal.get("result", {}).get("candidate_count")
            if isinstance(terminal.get("result"), dict)
            else None
        ),
        "idempotent_replay": True,
        "balance": {"before": before, "after": after, "expected": expected, "matches": matches},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live Opportunity Scan API smoke test.")
    parser.add_argument("--base-url", default=os.getenv("DEEPALPHA_API_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("DEEPALPHA_API_KEY", ""))
    parser.add_argument("--category", default="All")
    parser.add_argument("--language", choices=["ru", "en"], default="en")
    parser.add_argument("--scan-limit", type=int, default=100)
    parser.add_argument("--result-limit", type=int, default=10)
    parser.add_argument("--min-score", type=int, default=52)
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--strict-balance", action="store_true")
    args = parser.parse_args()
    if not args.base_url or not args.api_key:
        parser.error("--base-url and --api-key are required or set matching environment variables")
    return args


def main() -> int:
    try:
        result = run(parse_args())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
