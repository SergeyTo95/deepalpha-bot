#!/usr/bin/env python3
"""Production smoke for a dedicated Developer API test project.

This script is intentionally manual-provider only. It never sends TON, never charges a card,
and refuses to run unless DEEPALPHA_COMMERCIAL_SMOKE_ENABLED=true.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


def required(name: str) -> str:
    value = str(os.getenv(name, "") or "").strip()
    if not value:
        raise RuntimeError(f"missing_{name.lower()}")
    return value


def json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    cookie: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Cookie": cookie}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["X-DeepAlpha-Portal"] = "1"
    headers.update(extra_headers or {})
    request = urllib.request.Request(base_url + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"http_{exc.code}:{path}:{detail[:500]}") from exc


def admin_form(base_url: str, path: str, *, cookie: str, payload: Optional[Dict[str, Any]] = None) -> None:
    data = urllib.parse.urlencode(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": cookie,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in {200, 302, 303}:
                raise RuntimeError(f"admin_http_{response.status}:{path}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"admin_http_{exc.code}:{path}:{detail[:500]}") from exc


def project_from_overview(overview: Dict[str, Any], project_id: int) -> Dict[str, Any]:
    for project in overview.get("projects") or []:
        if int(project.get("id") or 0) == project_id:
            return project
    raise RuntimeError("dedicated_project_not_found")


def ledger_purchase_count(base_url: str, cookie: str, project_id: int, invoice_id: str) -> int:
    overview = json_request(base_url, "/app-api/v1/developer/overview", cookie=cookie)
    project = project_from_overview(overview, project_id)
    count = 0
    for entry in project.get("recent_ledger") or []:
        if str(entry.get("event_type") or "") != "purchase":
            continue
        key = str(entry.get("idempotency_key") or "")
        metadata = entry.get("metadata") or {}
        if key == f"invoice:{invoice_id}" or str(metadata.get("invoice_id") or "") == invoice_id:
            count += 1
    return count


def main() -> int:
    if str(os.getenv("DEEPALPHA_COMMERCIAL_SMOKE_ENABLED", "")).strip().lower() != "true":
        raise RuntimeError("set_DEEPALPHA_COMMERCIAL_SMOKE_ENABLED=true_for_dedicated_manual_smoke")

    base_url = required("DEEPALPHA_SMOKE_BASE_URL").rstrip("/")
    portal_cookie = required("DEEPALPHA_SMOKE_PORTAL_COOKIE")
    admin_cookie = required("DEEPALPHA_SMOKE_ADMIN_COOKIE")
    project_id = int(required("DEEPALPHA_SMOKE_PROJECT_ID"))
    package_code = required("DEEPALPHA_SMOKE_PACKAGE_CODE")

    before = json_request(base_url, "/app-api/v1/developer/commercial/overview", cookie=portal_cookie)
    if before.get("payment_provider") != "manual":
        raise RuntimeError("smoke_refuses_non_manual_provider")
    project_before = project_from_overview(before, project_id)
    balance_before = int(project_before.get("credit_balance") or project_before.get("spend", {}).get("balance") or 0)

    request_id = f"commercial-smoke-{project_id}"
    created = json_request(
        base_url,
        f"/app-api/v1/developer/projects/{project_id}/credit-invoices",
        method="POST",
        payload={"package_code": package_code, "client_request_id": request_id},
        cookie=portal_cookie,
        extra_headers={"Idempotency-Key": request_id},
    )
    invoice = created["invoice"]
    invoice_id = str(invoice["invoice_id"])
    credits = int(invoice["credits"])
    if invoice.get("status") not in {"awaiting_payment", "paid", "credited"}:
        raise RuntimeError(f"unexpected_invoice_status:{invoice.get('status')}")

    admin_form(
        base_url,
        f"/admin/api/credit-invoices/{urllib.parse.quote(invoice_id)}/mark-paid",
        cookie=admin_cookie,
        payload={"payment_reference": f"manual-smoke:{invoice_id}"},
    )
    admin_form(
        base_url,
        f"/admin/api/credit-invoices/{urllib.parse.quote(invoice_id)}/credit",
        cookie=admin_cookie,
    )

    after_first = json_request(base_url, "/app-api/v1/developer/commercial/overview", cookie=portal_cookie)
    balance_first = int(project_from_overview(after_first, project_id).get("spend", {}).get("balance") or 0)
    if balance_first != balance_before + credits:
        raise RuntimeError(f"balance_not_incremented_exactly:{balance_before}:{credits}:{balance_first}")

    admin_form(
        base_url,
        f"/admin/api/credit-invoices/{urllib.parse.quote(invoice_id)}/credit",
        cookie=admin_cookie,
    )
    after_second = json_request(base_url, "/app-api/v1/developer/commercial/overview", cookie=portal_cookie)
    balance_second = int(project_from_overview(after_second, project_id).get("spend", {}).get("balance") or 0)
    if balance_second != balance_first:
        raise RuntimeError("repeated_settlement_changed_balance")
    if ledger_purchase_count(base_url, portal_cookie, project_id, invoice_id) != 1:
        raise RuntimeError("purchase_ledger_entry_not_exactly_once")

    project = project_from_overview(after_second, project_id)
    if project.get("commercial_status") != "live_approved":
        if project.get("commercial_status") != "live_requested":
            json_request(
                base_url,
                f"/app-api/v1/developer/projects/{project_id}/live-request",
                method="POST",
                payload={
                    "company_name": "DeepAlpha Commercial Smoke",
                    "website": "https://example.com",
                    "use_case": "Dedicated production verification project for Developer API commercial launch.",
                    "expected_monthly_requests": 10,
                    "contact": "commercial-smoke",
                },
                cookie=portal_cookie,
            )
        admin_form(
            base_url,
            f"/admin/api/commercial/live/{project_id}/approve",
            cookie=admin_cookie,
            payload={"comment": "Dedicated commercial smoke approval"},
        )

    issued = json_request(
        base_url,
        f"/app-api/v1/developer/projects/{project_id}/live-keys",
        method="POST",
        payload={"name": "commercial-smoke", "scopes": ["account:read", "usage:read"]},
        cookie=portal_cookie,
    )
    raw_key = str(issued.get("key", {}).get("raw_key") or "")
    if not raw_key.startswith("da_live_"):
        raise RuntimeError("live_key_prefix_invalid")

    persisted = json_request(base_url, "/app-api/v1/developer/overview", cookie=portal_cookie)
    if "raw_key" in json.dumps(persisted):
        raise RuntimeError("raw_key_reappeared_after_one_time_response")

    print(json.dumps({
        "ok": True,
        "project_id": project_id,
        "invoice_id": invoice_id,
        "credits": credits,
        "balance_before": balance_before,
        "balance_after": balance_second,
        "ledger_purchase_entries": 1,
        "live_key_prefix": raw_key[:18],
        "provider": "manual",
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"commercial_smoke_failed:{exc}", file=sys.stderr)
        raise SystemExit(1)
