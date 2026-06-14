import sys
import types


if "requests" not in sys.modules:
    requests_stub = types.SimpleNamespace()

    class Timeout(Exception):
        pass

    requests_stub.exceptions = types.SimpleNamespace(Timeout=Timeout)

    def _missing(*args, **kwargs):
        raise RuntimeError("requests is not installed")

    requests_stub.get = _missing
    requests_stub.post = _missing
    sys.modules["requests"] = requests_stub
