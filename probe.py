"""One-shot Railway CPU acceptance. Uses synthetic prompts, then stops the worker."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


def main():
    os.environ["VELIA_FLASH_API_KEY"] = secrets.token_hex(32)
    os.environ["PORT"] = "8080"
    server = subprocess.Popen([sys.executable, str(Path(__file__).with_name("start.py"))])
    base = "http://127.0.0.1:8080"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = threading.Timer(900, server.kill)
    deadline.start()
    try:
        for _ in range(150):
            if server.poll() is not None:
                raise RuntimeError("worker stopped before health check")
            try:
                with opener.open(base + "/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(2)
        else:
            raise RuntimeError("worker startup exceeded 300 seconds")
        for label, prompt, expected in [
            ("arithmetic", "Сколько будет 17*23? Ответь только числом.", "391"),
            ("coding", "Write only a Python function add(a, b) that returns their sum.", "return"),
            ("russian", "Кратко объясни на русском, что такое переменная в Python.", None),
        ]:
            payload = {
                "model": "velia-flash", "stream": True, "max_tokens": 96,
                "stream_options": {"include_usage": True}, "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
                "thinking_budget_tokens": 0,
                "messages": [
                    {"role": "system", "content": "You are VELIA Flash. Answer accurately and concisely in the user's language. Return only the final answer."},
                    {"role": "user", "content": prompt},
                ],
            }
            request = urllib.request.Request(base + "/v1/chat/completions",
                data=json.dumps(payload).encode(), headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + os.environ["VELIA_FLASH_API_KEY"],
                })
            # Exercise the backend's actual context-budget API contract too.
            def post(path, data):
                req = urllib.request.Request(base + path,
                    data=json.dumps(data).encode(), headers=dict(request.headers))
                with opener.open(req, timeout=15) as response:
                    return json.load(response)
            rendered = post("/apply-template", {
                "messages": payload["messages"],
                "chat_template_kwargs": {"enable_thinking": False},
            })
            tokens = post("/tokenize", {"content": rendered["prompt"], "add_special": True})
            if not isinstance(tokens.get("tokens"), list):
                raise RuntimeError("tokenizer contract failed")
            print("BONSAI_PROBE_CONTEXT " + str(len(tokens["tokens"])), flush=True)
            started = time.monotonic()
            timer = threading.Timer(180, server.kill)
            timer.start()
            pieces, first_token, finish, usage = [], None, None, {}
            print("BONSAI_PROBE_START " + label, flush=True)
            try:
                with opener.open(request, timeout=180) as response:
                    for line in response:
                        if not line.startswith(b"data:"):
                            continue
                        body = line[5:].strip()
                        if body == b"[DONE]":
                            break
                        if not body:
                            continue
                        event = json.loads(body)
                        usage = event.get("usage") or usage
                        choice = (event.get("choices") or [{}])[0]
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            first_token = first_token or time.monotonic() - started
                            pieces.append(piece)
                        finish = choice.get("finish_reason") or finish
            finally:
                timer.cancel()
            answer = "".join(pieces).strip()
            elapsed = time.monotonic() - started
            passed = bool(answer and finish and "<think>" not in answer
                          and (expected is None or expected in answer))
            print("BONSAI_PROBE_RESULT " + json.dumps({
                "case": label, "ok": passed, "first_token_seconds": first_token,
                "elapsed_seconds": elapsed, "finish_reason": finish,
                "usage": usage, "answer": answer,
            }, ensure_ascii=False), flush=True)
            if not passed:
                raise RuntimeError("synthetic completion failed: " + label)
        print("BONSAI_PROBE_PASS", flush=True)
    finally:
        deadline.cancel()
        if server.poll() is None:
            server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    main()
