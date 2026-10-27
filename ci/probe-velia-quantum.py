"""Offline deployment check or explicit, bounded live Quantum acceptance.

Run explicitly in the account service's pre-deploy environment. Only a fixed
status is logged: never credentials, model output or exception messages.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import velia_quantum_service as quantum
from velia_quantum_provider import configuration, ERRORS, EXPERIENTIAL_URL, FREE_MODEL


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
    if "--config-only" in sys.argv:
        if "--voice" in sys.argv:
            print("VELIA_QUANTUM_CONFIG_FAILED incompatible_probe_modes", flush=True)
            return 1
        return check_configuration()
    voice = "--voice" in sys.argv
    config = configuration()
    if (config is None or config.provider != "experiential"
            or config.base_url != EXPERIENTIAL_URL or config.model != FREE_MODEL):
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
        print(("VELIA_QUANTUM_VOICE_PROBE_OK model=" if voice else "VELIA_QUANTUM_LIVE_PROBE_OK model=") + FREE_MODEL, flush=True)
        return 0
    reason = result.get("reason")
    print("VELIA_QUANTUM_LIVE_PROBE_FAILED " + (
        reason if isinstance(reason, str) and reason in ERRORS
        else "quantum_invalid_response"), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
