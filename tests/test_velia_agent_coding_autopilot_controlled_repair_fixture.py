from pathlib import Path


_TARGET = Path("docs/velia-autopilot-controlled-repair-smoke.txt")
_EXPECTED_MARKER = "VELIA_AUTOPILOT_REPAIR_OK"


def test_velia_autopilot_controlled_repair_marker():
    """Remain inert unless the dedicated acceptance smoke file exists."""
    if not _TARGET.exists():
        return

    lines = _TARGET.read_text(encoding="utf-8").splitlines()
    actual = lines[0].strip() if lines else ""

    assert actual == _EXPECTED_MARKER, (
        "Controlled VELIA Autopilot repair fixture: replace the first line of "
        f"{_TARGET} with {_EXPECTED_MARKER!r}. Preserve any following review-note line."
    )
