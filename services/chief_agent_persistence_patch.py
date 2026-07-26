import logging
import threading
from typing import Any

import agents.chief_agent as chief_agent_module

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()


def install() -> None:
    """Add a persist flag without changing existing ChiefAgent callers.

    The Developer API has its own durable job/result store. In the dedicated worker
    process, persist=False suppresses legacy Telegram/WebApp analysis and prediction
    writes while preserving the exact analysis pipeline. Existing calls keep the
    original persist=True behavior.
    """
    original = chief_agent_module.ChiefAgent.run
    if getattr(original, "_deepalpha_persist_flag", False):
        return

    def run_with_persist_flag(
        self,
        url: str,
        lang: str = "en",
        user_id: int = 0,
        user_context: str = "",
        persist: bool = True,
    ) -> Any:
        if persist:
            return original(
                self,
                url,
                lang=lang,
                user_id=user_id,
                user_context=user_context,
            )

        # The API worker processes jobs sequentially. The lock also makes direct
        # tests safe if two threads invoke the wrapper in the same process.
        with _LOCK:
            original_save_analysis = chief_agent_module.save_analysis
            original_track_prediction = self._track_prediction
            chief_agent_module.save_analysis = lambda *_args, **_kwargs: None
            self._track_prediction = lambda *_args, **_kwargs: None
            try:
                return original(
                    self,
                    url,
                    lang=lang,
                    user_id=user_id,
                    user_context=user_context,
                )
            finally:
                chief_agent_module.save_analysis = original_save_analysis
                self._track_prediction = original_track_prediction

    run_with_persist_flag._deepalpha_persist_flag = True
    run_with_persist_flag._deepalpha_original_run = original
    chief_agent_module.ChiefAgent.run = run_with_persist_flag
    logger.info("CHIEF_AGENT_PERSISTENCE_PATCH_INSTALLED")
