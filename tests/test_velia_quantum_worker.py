from pathlib import Path

from quantum_worker.start import worker_args


def test_quantum_worker_is_cpu_only_and_uses_quantum_alias(monkeypatch):
    monkeypatch.delenv("VELIA_QUANTUM_CONTEXT_TOKENS", raising=False)
    monkeypatch.delenv("VELIA_QUANTUM_PROMPT_CACHE_MIB", raising=False)
    args = worker_args(
        Path("/tmp/key"),
        Path("/model/velia-quantum.gguf"),
    )

    assert args[0] == "/opt/quantum/llama-server"
    assert args[args.index("-m") + 1] == "/model/velia-quantum.gguf"
    assert args[args.index("--alias") + 1] == "velia-quantum"
    assert args[args.index("-ngl") + 1] == "0"
    assert args[args.index("-c") + 1] == "16384"
    assert args[args.index("--cache-ram") + 1] == "1024"
    assert "--metrics" in args
    assert "--no-webui" in args


def test_quantum_worker_runtime_knobs_are_bounded(monkeypatch):
    monkeypatch.setenv("VELIA_QUANTUM_CPU_THREADS", "999")
    monkeypatch.setenv("VELIA_QUANTUM_CONTEXT_TOKENS", "999999")
    monkeypatch.setenv("VELIA_QUANTUM_PROMPT_CACHE_MIB", "999999")
    monkeypatch.setenv("VELIA_QUANTUM_PARALLEL", "99")

    args = worker_args(Path("/tmp/key"), Path("/tmp/model.gguf"))

    assert args[args.index("-t") + 1] == "24"
    assert args[args.index("-tb") + 1] == "24"
    assert args[args.index("-c") + 1] == "32768"
    assert args[args.index("--cache-ram") + 1] == "4096"
    assert args[args.index("--parallel") + 1] == "2"
