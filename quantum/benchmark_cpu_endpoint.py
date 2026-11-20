#!/usr/bin/env python3
"""Measure VELIA Quantum CPU endpoint latency and warm decode speed."""
from __future__ import annotations

import argparse
import json
import time
from urllib.parse import urlsplit

import requests


def _base_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid base URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials/query/fragment")
    return value


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _token_count(session: requests.Session, base: str, text: str) -> int:
    response = session.post(
        base + "/tokenize",
        json={"content": text, "add_special": True},
        timeout=(5, 60),
    )
    response.raise_for_status()
    payload = response.json()
    tokens = payload.get("tokens")
    if not isinstance(tokens, list):
        raise RuntimeError("/tokenize returned no token list")
    return len(tokens)


def build_2k_prompt(session: requests.Session, base: str) -> tuple[str, int]:
    unit = (
        "VELIA Quantum runtime benchmark. Preserve this neutral context and "
        "answer the final instruction only. "
    )
    pieces = [unit] * 700
    lo, hi = 1, len(pieces)
    best_text, best_tokens = unit, _token_count(session, base, unit)
    while lo <= hi:
        mid = (lo + hi) // 2
        text = " ".join(pieces[:mid])
        count = _token_count(session, base, text)
        if count <= 2050:
            best_text, best_tokens = text, count
            lo = mid + 1
        else:
            hi = mid - 1
    if best_tokens < 1800:
        raise RuntimeError(
            f"could not construct ~2K-token prompt; got {best_tokens}"
        )
    return (
        best_text
        + "\nFinal instruction: reply with exactly the word READY.",
        best_tokens,
    )


def stream_ttft(
    session: requests.Session,
    base: str,
    prompt: str,
) -> tuple[float, float, str]:
    payload = {
        "model": "velia-quantum",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16,
        "temperature": 0.0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    start = time.perf_counter()
    first = None
    pieces = []
    with session.post(
        base + "/v1/chat/completions",
        json=payload,
        timeout=(5, 180),
        stream=True,
    ) as response:
        response.raise_for_status()
        for raw in response.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            event = json.loads(body)
            choice = (event.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                if first is None:
                    first = time.perf_counter()
                pieces.append(content)
    end = time.perf_counter()
    if first is None:
        raise RuntimeError("stream produced no content")
    return first - start, end - start, "".join(pieces).strip()


def warm_decode(
    session: requests.Session,
    base: str,
) -> tuple[float, int, float]:
    # Use raw completion so llama.cpp reports deterministic decode timing while
    # avoiding an early conversational stop.
    prompt = (
        "Continue this comma-separated integer sequence with integers only: "
        + ",".join(str(i) for i in range(1, 65))
        + ","
    )
    payload = {
        "prompt": prompt,
        "n_predict": 192,
        "temperature": 0.0,
        "ignore_eos": True,
        "cache_prompt": True,
    }

    # First request warms model pages and prompt cache.
    warm = session.post(
        base + "/completion",
        json=payload,
        timeout=(5, 240),
    )
    warm.raise_for_status()

    start = time.perf_counter()
    response = session.post(
        base + "/completion",
        json=payload,
        timeout=(5, 240),
    )
    elapsed = time.perf_counter() - start
    response.raise_for_status()
    data = response.json()

    timings = data.get("timings") if isinstance(data.get("timings"), dict) else {}
    predicted = int(
        timings.get("predicted_n")
        or data.get("tokens_predicted")
        or 0
    )
    reported_tps = float(timings.get("predicted_per_second") or 0.0)
    if predicted <= 0:
        raise RuntimeError("completion response has no predicted token count")
    if reported_tps <= 0:
        reported_tps = predicted / max(elapsed, 1e-9)
    return reported_tps, predicted, elapsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--report")
    args = parser.parse_args(argv)

    base = _base_url(args.base_url)
    session = requests.Session()
    session.trust_env = False
    session.headers.update(_headers(args.api_key))

    health = session.get(base + "/health", timeout=(5, 30))
    health.raise_for_status()

    prompt, prompt_tokens = build_2k_prompt(session, base)
    ttft, ttft_total, ttft_text = stream_ttft(session, base, prompt)
    tps, predicted, decode_elapsed = warm_decode(session, base)

    metrics_status = None
    try:
        metrics = session.get(base + "/metrics", timeout=(5, 30))
        metrics_status = metrics.status_code
    except requests.RequestException:
        metrics_status = None

    report = {
        "ok": True,
        "model": "velia-quantum",
        "input_tokens_ttft": prompt_tokens,
        "ttft_seconds_2k": round(ttft, 6),
        "ttft_request_seconds": round(ttft_total, 6),
        "ttft_response": ttft_text,
        "warm_output_tokens_per_second": round(tps, 6),
        "warm_output_tokens": predicted,
        "warm_decode_request_seconds": round(decode_elapsed, 6),
        "metrics_endpoint_status": metrics_status,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        from pathlib import Path
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
