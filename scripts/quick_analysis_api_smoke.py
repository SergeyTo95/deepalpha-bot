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


def _request_json(
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
        "User-Agent": "DeepAlpha-Quick-Analysis-Smoke/1.0",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            result = json.loads(text)
            result["_http_status"] = int(response.status)
            return result
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            result = json.loads(text)
        except Exception:
            result = {"ok": False, "error": text[:500] or exc.reason}
        result["_http_status"] = int(exc.code)
        return result


def validate_terminal_payload(payload: Dict[str, Any], expected: str = "either") -> Dict[str, Any]:
    status = str(payload.get("status") or "")
    if status not in {"success", "error"}:
        raise AssertionError(f"terminal status required, got {status!r}")
    if expected != "either" and status != expected:
        raise AssertionError(f"expected {expected}, got {status}")

    credits = payload.get("credits") if isinstance(payload.get("credits"), dict) else {}
    reserved = int(credits.get("reserved") or 0)
    charged = int(credits.get("charged") or 0)
    refunded = int(credits.get("refunded") or 0)
    reservation_status = str(credits.get("reservation_status") or "")
    if reserved <= 0:
        raise AssertionError("reserved credits must be positive")

    if status == "success":
        if charged != reserved:
            raise AssertionError(f"success must charge all reserved credits: {charged} != {reserved}")
        if refunded != 0 or reservation_status != "charged":
            raise AssertionError("success must end with charged reservation and no refund")
        if not isinstance(payload.get("result"), dict) or not payload["result"]:
            raise AssertionError("success response must include public result")
    else:
        if refunded != reserved:
            raise AssertionError(f"error must refund all reserved credits: {refunded} != {reserved}")
        if reservation_status != "refunded":
            raise AssertionError("error must end with refunded reservation")
        if not payload.get("error"):
            raise AssertionError("error response must include a stable error code")

    return {
        "status": status,
        "reserved": reserved,
        "charged": charged,
        "refunded": refunded,
        "reservation_status": reservation_status,
    }


def _balance(account_payload: Dict[str, Any]) -> int:
    client = account_payload.get("client") if isinstance(account_payload.get("client"), dict) else {}
    return int(client.get("credit_balance") or 0)


def run_smoke(args: argparse.Namespace) -> Dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    before = _request_json("GET", f"{base_url}/api/v1/account", api_key=args.api_key)
    if not before.get("ok"):
        raise RuntimeError(f"account check failed: {before}")
    before_balance = _balance(before)

    idempotency_key = args.idempotency_key or f"smoke:{uuid.uuid4().hex}"
    submitted = _request_json(
        "POST",
        f"{base_url}/api/v1/analyses",
        api_key=args.api_key,
        idempotency_key=idempotency_key,
        payload={
            "market_url": args.market_url,
            "mode": "quick",
            "language": args.language,
        },
    )
    if not submitted.get("ok"):
        raise RuntimeError(f"analysis submission failed: {submitted}")
    job_id = str(submitted.get("job_id") or "")
    if not job_id.startswith("job_"):
        raise RuntimeError(f"invalid job id: {job_id!r}")

    deadline = time.monotonic() + args.wait_seconds
    terminal: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        current = _request_json(
            "GET",
            f"{base_url}/api/v1/analyses/{job_id}",
            api_key=args.api_key,
        )
        status = str(current.get("status") or "")
        print(json.dumps({
            "job_id": job_id,
            "status": status,
            "progress": current.get("progress"),
            "credits": current.get("credits"),
        }, ensure_ascii=False))
        if status in {"success", "error"}:
            terminal = current
            break
        time.sleep(args.poll_seconds)

    if terminal is None:
        raise TimeoutError(f"job {job_id} did not finish within {args.wait_seconds}s")
    settlement = validate_terminal_payload(terminal, expected=args.expect)

    after = _request_json("GET", f"{base_url}/api/v1/account", api_key=args.api_key)
    if not after.get("ok"):
        raise RuntimeError(f"final account check failed: {after}")
    after_balance = _balance(after)
    expected_balance = before_balance - settlement["charged"]
    if settlement["status"] == "error":
        expected_balance = before_balance

    balance_matches = after_balance == expected_balance
    if args.strict_balance and not balance_matches:
        raise AssertionError(
            f"balance mismatch: before={before_balance} after={after_balance} expected={expected_balance}; "
            "use a dedicated smoke project or omit --strict-balance when concurrent requests are possible"
        )

    return {
        "ok": True,
        "job_id": job_id,
        "idempotency_key": idempotency_key,
        "settlement": settlement,
        "balance": {
            "before": before_balance,
            "after": after_balance,
            "expected": expected_balance,
            "matches": balance_matches,
        },
        "result": terminal.get("result") if settlement["status"] == "success" else None,
        "error": terminal.get("error") if settlement["status"] == "error" else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live DeepAlpha Quick Analysis billing smoke test.")
    parser.add_argument("--base-url", default=os.getenv("DEEPALPHA_API_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("DEEPALPHA_API_KEY", ""))
    parser.add_argument("--market-url", default=os.getenv("DEEPALPHA_SMOKE_MARKET_URL", ""))
    parser.add_argument("--language", choices=["ru", "en"], default=os.getenv("DEEPALPHA_SMOKE_LANGUAGE", "en"))
    parser.add_argument("--expect", choices=["success", "error", "either"], default=os.getenv("DEEPALPHA_SMOKE_EXPECT", "success"))
    parser.add_argument("--wait-seconds", type=int, default=240)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--strict-balance", action="store_true")
    args = parser.parse_args()
    if not args.base_url or not args.api_key or not args.market_url:
        parser.error("--base-url, --api-key and --market-url are required (or set matching environment variables)")
    return args


def main() -> int:
    try:
        result = run_smoke(parse_args())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
