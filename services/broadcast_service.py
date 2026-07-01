import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

try:
    from aiogram.utils.exceptions import BotBlocked, ChatNotFound, RetryAfter, UserDeactivated
except Exception:  # pragma: no cover - aiogram is available in production
    BotBlocked = ChatNotFound = RetryAfter = UserDeactivated = Exception

logger = logging.getLogger(__name__)

DEFAULT_BROADCAST_TEXT = """🚀 DeepAlpha становится вашим личным AI-советником по поиску возможностей

Мы превращаем DeepAlpha в AI-ассистента нового типа — как ChatGPT, только заточенного под крипту, спорт, киберспорт, ставки, политику и prediction markets.

Что уже умеет DeepAlpha:

🧠 Анализировать события
Бот разбирает вероятность, аргументы, риски, новости и показывает, где может быть потенциальный edge.

₿ Крипта
Можно разбирать BTC, ETH, альткоины, тренды, волатильность, рыночный контекст и идеи для наблюдения.

⚽ Спорт и киберспорт
DeepAlpha помогает анализировать матчи, линии, коэффициенты, форму команд и важный контекст.

🗳 Политика и глобальные события
Бот помогает оценивать сложные события без шума, эмоций и лишней воды.

🎁 Airdrop Points уже активны
За успешные анализы ты уже можешь получать DeepAlpha Points.

Эти баллы будут учитываться в будущей экосистеме DeepAlpha и связаны с будущей монетой проекта.

🔥 Скоро откроется Live режим
Это будет полноценный AI-чат внутри бота: задаёшь вопрос — получаешь разбор как от личного аналитика.

DeepAlpha — это не просто бот с прогнозами.
Это ваш личный AI-советник по крипте, ставкам, спорту и событиям.

Открой бота, сделай анализ и начни собирать Points уже сейчас 👇"""

FORBIDDEN_BROADCAST_PHRASES = {
    "guaranteed profit",
    "гарантированный заработок",
    "exact listing date",
    "exact token price",
    "guaranteed allocation",
}


def filter_broadcast_recipients(users: Iterable[Dict[str, Any]]) -> List[int]:
    seen: set[int] = set()
    recipients: List[int] = []
    for user in users or []:
        raw_uid = user.get("user_id") if isinstance(user, dict) else None
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            continue
        if uid <= 0 or uid in seen:
            continue
        if bool(user.get("is_banned")):
            continue
        seen.add(uid)
        recipients.append(uid)
    return recipients


@dataclass
class BroadcastState:
    status: str = "idle"
    total: int = 0
    sent: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_error: str = ""
    current_rate: float = 0.0
    _start_ts: Optional[float] = field(default=None, repr=False)
    cancel_requested: bool = field(default=False, repr=False)

    def start(self, total: int) -> None:
        self.status = "running"
        self.total = int(total)
        self.sent = self.failed = self.blocked = self.skipped = 0
        self.started_at = datetime.utcnow().isoformat()
        self.finished_at = None
        self.last_error = ""
        self.current_rate = 0.0
        self._start_ts = time.monotonic()
        self.cancel_requested = False

    def finish(self, status: str = "finished") -> None:
        self.status = status
        self.finished_at = datetime.utcnow().isoformat()
        if self._start_ts:
            elapsed = max(time.monotonic() - self._start_ts, 0.001)
            self.current_rate = round((self.sent + self.failed + self.blocked + self.skipped) / elapsed, 2)

    def mark_sent(self) -> None:
        self.sent += 1
        self._update_rate()

    def mark_failed(self, error: str = "") -> None:
        self.failed += 1
        self.last_error = str(error)[:300]
        self._update_rate()

    def mark_blocked(self, error: str = "") -> None:
        self.blocked += 1
        self.last_error = str(error)[:300]
        self._update_rate()

    def mark_skipped(self) -> None:
        self.skipped += 1
        self._update_rate()

    def _update_rate(self) -> None:
        if self._start_ts:
            elapsed = max(time.monotonic() - self._start_ts, 0.001)
            self.current_rate = round((self.sent + self.failed + self.blocked + self.skipped) / elapsed, 2)

    def as_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in ("status", "total", "sent", "failed", "blocked", "skipped", "current_rate", "started_at", "finished_at", "last_error")}


broadcast_state = BroadcastState()


def default_broadcast_rate() -> float:
    try:
        return max(1.0, min(20.0, float(os.getenv("BROADCAST_MESSAGES_PER_SECOND", "20"))))
    except ValueError:
        return 20.0


async def send_broadcast(bot: Any, recipients: List[int], text: str, reply_markup: Any = None, state: BroadcastState = broadcast_state, rate_per_second: Optional[float] = None) -> None:
    rate = rate_per_second or default_broadcast_rate()
    delay = 1.0 / rate
    state.start(len(recipients))
    try:
        for idx, user_id in enumerate(recipients, start=1):
            if state.cancel_requested:
                state.finish("cancelled")
                return
            try:
                await bot.send_message(user_id, text, reply_markup=reply_markup, disable_web_page_preview=True)
                state.mark_sent()
            except RetryAfter as e:
                timeout = float(getattr(e, "timeout", 1) or 1)
                logger.warning("broadcast flood wait %.1fs at user_id=%s", timeout, user_id)
                await asyncio.sleep(timeout)
                state.mark_failed(f"retry_after:{timeout}")
            except (BotBlocked, ChatNotFound, UserDeactivated) as e:
                state.mark_blocked(str(e))
            except Exception as e:
                state.mark_failed(str(e))
                logger.warning("broadcast send failed user_id=%s: %s", user_id, e)
            if idx % 100 == 0:
                logger.info("broadcast progress %s/%s sent=%s failed=%s blocked=%s", idx, state.total, state.sent, state.failed, state.blocked)
            await asyncio.sleep(delay)
        state.finish("finished")
    except Exception as e:
        state.last_error = str(e)[:300]
        logger.exception("broadcast fatal error")
        state.finish("failed")
