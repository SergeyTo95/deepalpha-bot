"""Serial public Flash quality probes; synthetic questions, no account credentials.

Records real outputs for review against understanding-cases.json. Transport success
does not imply a quality pass, and the script deliberately does not assign one.
"""
import argparse
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", dest="selected")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    url = urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.path:
        parser.error("Use a public HTTPS gateway origin")
    cases = json.loads(Path(__file__).with_name("understanding-cases.json").read_text())
    if args.selected:
        cases = [case for case in cases if case["id"] in args.selected]
        if {case["id"] for case in cases} != set(args.selected):
            parser.error("Unknown case")
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {"Origin": base, "X-Velia-Request": "1", "Sec-Fetch-Site": "same-origin"}
    with client.open(Request(base + "/web-api/v1/guest", headers=headers), timeout=30) as response:
        profile = json.load(response)
    if not profile.get("ok") or "velia-flash" not in profile.get("models", []):
        raise RuntimeError("Guest Flash is unavailable")
    if profile["remaining"] < len(cases):
        raise RuntimeError("Insufficient guest quota for selected cases")
    report = {"base_url": base, "started_at": datetime.now(timezone.utc).isoformat(),
        "remaining_before": profile["remaining"], "cases": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        print("START " + case["id"], flush=True)
        row = {**case, "http_status": None, "answer": "", "done": False,
            "sources": [], "finish_reason": None, "quality_review": "pending"}
        started = time.monotonic()
        body = json.dumps({"model": "velia-flash", "stream": True,
            "messages": case["messages"]}, ensure_ascii=False).encode()
        try:
            request = Request(base + "/web-api/v1/guest/chat/completions", data=body,
                headers={**headers, "Content-Type": "application/json"})
            with client.open(request, timeout=360) as response:
                row["http_status"] = response.status
                for line in response:
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        row["done"] = True
                        continue
                    event = json.loads(data)
                    if event.get("error"):
                        row["error"] = event["error"]
                    if "web_search" in event:
                        row["sources"] = event["web_search"].get("sources", [])
                    for choice in event.get("choices", []):
                        delta = choice.get("delta", {}).get("content")
                        if isinstance(delta, str):
                            row["answer"] += delta
                        if choice.get("finish_reason"):
                            row["finish_reason"] = choice["finish_reason"]
        except HTTPError as error:
            row["http_status"] = error.code
            row["error"] = error.read(2048).decode(errors="replace")
        row["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["cases"].append(row)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"case": row["id"], "status": row["http_status"],
            "done": row["done"], "seconds": row["elapsed_seconds"],
            "answer": row["answer"][:260]}, ensure_ascii=False), flush=True)
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
