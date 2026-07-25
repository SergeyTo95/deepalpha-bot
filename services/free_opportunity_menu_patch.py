import logging
from typing import Any, Optional

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from services.free_opportunity_runtime_patch import format_free_opportunity_card

logger = logging.getLogger(__name__)

RU_BUTTON = "🔍 Найти возможности"
EN_BUTTON = "🔍 Find opportunities"
LEGACY_RU_BUTTON = "🔮 Личный сигнал"
LEGACY_EN_BUTTON = "🔮 Personal signal"


def install(telegram_module: Any) -> None:
    """Expose the zero-cost scanner and replace the legacy billed personal signal.

    PR #320 changed OpportunityAgent to a free deterministic scanner by default,
    but the Telegram menu still hid it behind the legacy Personal signal handler,
    which retained the old token checks. This patch gives the feature an explicit
    menu entry and removes the legacy handler from the dispatcher registry.
    """
    if getattr(telegram_module, "_deepalpha_free_opportunity_menu_installed", False):
        return

    _install_analysis_keyboard(telegram_module)
    _install_other_analyses_keyboard(telegram_module)
    removed = _remove_legacy_personal_signal_handler(telegram_module)

    async def free_opportunity_handler(message, state=None):
        await run_free_opportunity(message, state, telegram_module)

    dispatcher = telegram_module.dp
    dispatcher.register_message_handler(
        free_opportunity_handler,
        lambda message: str(getattr(message, "text", "") or "")
        in {RU_BUTTON, EN_BUTTON, LEGACY_RU_BUTTON, LEGACY_EN_BUTTON},
        state="*",
    )
    dispatcher.register_message_handler(
        free_opportunity_handler,
        commands=["opportunities", "find_opportunities"],
        state="*",
    )

    telegram_module._deepalpha_free_opportunity_menu_installed = True
    telegram_module._free_opportunity_handler = free_opportunity_handler
    logger.info("FREE_OPPORTUNITY_MENU_INSTALLED legacy_handlers_removed=%s", removed)


def _install_analysis_keyboard(telegram_module: Any) -> None:
    original = getattr(telegram_module, "get_analysis_keyboard", None)
    if not callable(original) or getattr(original, "_deepalpha_free_opportunity", False):
        return

    def get_analysis_keyboard(user_id: int) -> ReplyKeyboardMarkup:
        lang = telegram_module.get_user_lang(user_id)
        keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
        if lang == "ru":
            keyboard.add(KeyboardButton("⚡️ Быстрый анализ"), KeyboardButton("💡 Сигнал часа"))
            keyboard.add(KeyboardButton(RU_BUTTON))
            keyboard.add(KeyboardButton(telegram_module.LIVE_ANALYST_BUTTON), KeyboardButton("🏁 Market Recap"))
            keyboard.add(KeyboardButton("📜 История"), KeyboardButton("➕ Другие анализы"))
            keyboard.add(KeyboardButton("⬅️ Назад"))
        else:
            keyboard.add(KeyboardButton("⚡️ Quick Analysis"), KeyboardButton("💡 Signal of the hour"))
            keyboard.add(KeyboardButton(EN_BUTTON))
            keyboard.add(KeyboardButton(telegram_module.LIVE_ANALYST_BUTTON), KeyboardButton("🏁 Market Recap"))
            keyboard.add(KeyboardButton("📜 History"), KeyboardButton("➕ Other Analyses"))
            keyboard.add(KeyboardButton("⬅️ Back"))
        return keyboard

    get_analysis_keyboard._deepalpha_free_opportunity = True
    telegram_module.get_analysis_keyboard = get_analysis_keyboard


def _install_other_analyses_keyboard(telegram_module: Any) -> None:
    original = getattr(telegram_module, "get_other_analyses_keyboard", None)
    if not callable(original) or getattr(original, "_deepalpha_free_opportunity", False):
        return

    def get_other_analyses_keyboard(user_id: int) -> ReplyKeyboardMarkup:
        lang = telegram_module.get_user_lang(user_id)
        top_enabled = bool(telegram_module._is_top_analysis_enabled())
        keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
        if lang == "ru":
            if top_enabled:
                keyboard.add(KeyboardButton("🔥 Top Analysis"), KeyboardButton("🪙 Крипто анализ"))
                keyboard.add(KeyboardButton(RU_BUTTON), KeyboardButton("🏆 Топ"))
                keyboard.add(KeyboardButton("📘 Как читать анализ"))
            else:
                keyboard.add(KeyboardButton("🪙 Крипто анализ"), KeyboardButton(RU_BUTTON))
                keyboard.add(KeyboardButton("🏆 Топ"), KeyboardButton("📘 Как читать анализ"))
            keyboard.add(KeyboardButton("⬅️ Назад к анализу"))
        else:
            if top_enabled:
                keyboard.add(KeyboardButton("🔥 Top Analysis"), KeyboardButton("🪙 Crypto Analysis"))
                keyboard.add(KeyboardButton(EN_BUTTON), KeyboardButton("🏆 Top"))
                keyboard.add(KeyboardButton("📘 How to read the analysis"))
            else:
                keyboard.add(KeyboardButton("🪙 Crypto Analysis"), KeyboardButton(EN_BUTTON))
                keyboard.add(KeyboardButton("🏆 Top"), KeyboardButton("📘 How to read the analysis"))
            keyboard.add(KeyboardButton("⬅️ Back to analysis"))
        return keyboard

    get_other_analyses_keyboard._deepalpha_free_opportunity = True
    telegram_module.get_other_analyses_keyboard = get_other_analyses_keyboard


def _remove_legacy_personal_signal_handler(telegram_module: Any) -> int:
    registry = getattr(getattr(telegram_module, "dp", None), "message_handlers", None)
    handlers = getattr(registry, "handlers", None)
    if not isinstance(handlers, list):
        logger.warning("FREE_OPPORTUNITY_LEGACY_HANDLER_REGISTRY_UNAVAILABLE")
        return 0

    kept = []
    removed = 0
    for item in handlers:
        callback = getattr(item, "handler", None) or getattr(item, "callback", None)
        if getattr(callback, "__name__", "") == "personal_signal_handler":
            removed += 1
            continue
        kept.append(item)
    handlers[:] = kept
    return removed


async def run_free_opportunity(message: Any, state: Optional[Any], telegram_module: Any) -> None:
    if state is not None:
        finish = getattr(state, "finish", None)
        if callable(finish):
            await finish()

    telegram_module._register_user(message)
    user_id = int(message.from_user.id)
    if telegram_module._check_banned(message):
        await message.answer(telegram_module.t(user_id, "banned"))
        return

    lang = telegram_module.get_user_lang(user_id)
    status_text = (
        "🔍 Бесплатно проверяю рынки Polymarket…\n\nKimi и Gemini не запускаются."
        if lang == "ru"
        else "🔍 Scanning Polymarket markets for free…\n\nKimi and Gemini are not being called."
    )
    status_message = await message.answer(status_text)

    try:
        agent = telegram_module.OpportunityAgent()
        result = agent.run(lang=lang, limit=5)
        result = result if isinstance(result, dict) else {}
        result["lang"] = lang
        result["provider_calls"] = 0
        result["paid_ai_used"] = False
        result["free_user_initiated"] = True
        text = format_free_opportunity_card(result, lang=lang)
    except Exception as exc:
        logger.exception("FREE_OPPORTUNITY_USER_SCAN_FAILED type=%s", exc.__class__.__name__)
        text = (
            "❌ Не удалось получить список рынков. Попробуй повторить сканирование позже.\n\nAI-запросы не выполнялись."
            if lang == "ru"
            else "❌ Could not load the market shortlist. Try again later.\n\nNo AI requests were made."
        )
    finally:
        try:
            await status_message.delete()
        except Exception:
            pass

    try:
        telegram_module.increment_user_stat(user_id, "total_opportunities")
    except Exception:
        pass

    markup = telegram_module.private_reply_markup(
        message,
        telegram_module.get_analysis_keyboard(user_id),
    )
    await message.answer(
        text,
        reply_markup=markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
