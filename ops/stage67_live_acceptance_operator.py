from __future__ import annotations

# Temporary fail-closed shim: Railway snapshots have repeatedly preserved this
# historical command. Route it to the read-only resume probe so no arm,
# dispatch, enqueue, PR, CI, or reviewer mutation can occur during recovery.
from stage67_resume_probe import main


if __name__ == "__main__":
    raise SystemExit(main())
