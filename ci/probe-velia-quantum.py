"""Offline deployment check or explicit, bounded live Quantum acceptance.

Run explicitly in the account service's pre-deploy environment. Only a fixed
status is logged: never credentials, model output or exception messages.
"""
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import velia_quantum_service as quantum
from velia_quantum_provider import configuration, ERRORS, EXPERIENTIAL_URL, FREE_MODEL, EXPERIENTIAL_MODELS, payload, require_free_zai, QuantumError, StreamDecoder
from velia_quantum_turn import Turn
from velia_quantum_transport import HEADERS, sync_error_details


def compare_requests():
    """Two synthetic calls, no retries/tools executed, no secret/output logging.

    Explicit operator diagnostic, never a recurring deployment readiness gate.
    A provider refusal is reported without blocking an otherwise healthy release.
    """
    config = configuration()
    if config is None or config.provider != "zai":
        print("VELIA_QUANTUM_COMPARE_SKIPPED reason=unsupported_configuration", flush=True)
        return 1
    messages = [{"role": "user", "content": "Ответь одним словом: Готова."}]
    bodies = [("minimal", payload(config, messages, stream=True, max_tokens=256)),
        ("native_tools", Turn(config, messages, search_enabled=True, max_tokens=256).body())]
    outcomes = []
    with quantum._provider_client(config) as client:
        for shape, body in bodies:
            started = time.monotonic()
            status, code, outcome = 0, "none", "transport_error"
            try:
                require_free_zai(config)
                with client.post(config.base_url + "/chat/completions", json=body,
                        headers={**HEADERS, "Authorization": "Bearer " + config.api_key},
                        timeout=(3, 15), stream=True, allow_redirects=False) as response:
                    status = response.status_code
                    if status != 200:
                        code, _reason = sync_error_details(response)
                        outcome = "rejected"
                    else:
                        decoder = StreamDecoder()
                        size = 0
                        if "text/event-stream" not in response.headers.get("Content-Type", ""):
                            raise QuantumError("quantum_invalid_response")
                        for chunk in response.iter_content(1):
                            size += len(chunk)
                            if size > 32768 or time.monotonic() - started > 20:
                                raise QuantumError("quantum_timeout")
                            decoder.feed(chunk)
                        decoder.finish()
                        outcome = "accepted" if decoder.finish_reason in {"stop", "tool_calls"} else "incomplete"
            except QuantumError as exc:
                outcome = exc.code if exc.code in ERRORS else "quantum_invalid_response"
            except Exception:
                outcome = "transport_error"
            outcomes.append(outcome)
            print("VELIA_QUANTUM_COMPARE shape=%s status=%d provider_code=%s outcome=%s total_ms=%d" %
                (shape, status, code, outcome, round((time.monotonic() - started) * 1000)), flush=True)
    print("VELIA_QUANTUM_COMPARE_DONE calls=2 all_accepted=%s" % str(all(x == "accepted" for x in outcomes)).lower(), flush=True)
    return 0


def check_configuration():
    """Validate startup configuration without depending on provider availability.

    Runtime generation still verifies the exact key's hard zero-spend cap
    before every completion. This check does not claim live/free availability.
    """
    enabled = os.getenv("VELIA_QUANTUM_ENABLED", "").strip().lower() in {"true", "1", "yes", "on"}
    if not enabled:
        print("VELIA_QUANTUM_CONFIG_DISABLED", flush=True)
        return 0
    config = configuration()
    if config is None:
        print("VELIA_QUANTUM_CONFIG_FAILED quantum_unavailable", flush=True)
        return 1
    print("VELIA_QUANTUM_CONFIG_OK provider=" + config.provider, flush=True)
    return 0


def main():
    if "--compare-requests" in sys.argv:
        if "--config-only" in sys.argv or "--voice" in sys.argv:
            print("VELIA_QUANTUM_COMPARE_SKIPPED reason=incompatible_probe_modes", flush=True)
            return 1
        return compare_requests()
    if "--config-only" in sys.argv:
        if "--voice" in sys.argv:
            print("VELIA_QUANTUM_CONFIG_FAILED incompatible_probe_modes", flush=True)
            return 1
        return check_configuration()
    voice = "--voice" in sys.argv
    config = configuration()
    if (config is None or not (config.provider == "zai" or config.provider == "experiential"
            and config.base_url == EXPERIENTIAL_URL and config.model in EXPERIENTIAL_MODELS)):
        print("VELIA_QUANTUM_LIVE_PROBE_FAILED quantum_unavailable", flush=True)
        return 1
    # This process-local bound never changes the running chat's output limit.
    previous_limit = os.environ.get("VELIA_QUANTUM_MAX_OUTPUT_TOKENS")
    os.environ["VELIA_QUANTUM_MAX_OUTPUT_TOKENS"] = "1024"
    try:
        result = quantum.generate("Ответь одним словом: Готова.",
            request_id="railway-quantum-release-probe", **({"voice_turn": True} if voice else {}))
    except Exception:
        print("VELIA_QUANTUM_LIVE_PROBE_FAILED quantum_provider_error", flush=True)
        return 1
    finally:
        if previous_limit is None:
            os.environ.pop("VELIA_QUANTUM_MAX_OUTPUT_TOKENS", None)
        else:
            os.environ["VELIA_QUANTUM_MAX_OUTPUT_TOKENS"] = previous_limit
    if (result.get("ok") is True and result.get("finish_reason") == "stop"
            and str(result.get("text") or "").strip()
            and result.get("provider") == "velia-quantum"
            and result.get("model") == "velia-quantum"
            and result.get("estimated_cost_usd") == 0
            and result.get("fallback_used") is False):
        print(("VELIA_QUANTUM_VOICE_PROBE_OK model=" if voice else "VELIA_QUANTUM_LIVE_PROBE_OK model=") + config.model, flush=True)
        return 0
    reason = result.get("reason")
    print("VELIA_QUANTUM_LIVE_PROBE_FAILED " + (
        reason if isinstance(reason, str) and reason in ERRORS
        else "quantum_invalid_response"), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
