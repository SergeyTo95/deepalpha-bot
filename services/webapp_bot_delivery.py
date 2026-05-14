import os
from typing import Any, Dict

from aiogram import Bot

from services.webapp_report_formatter import build_webapp_analysis_report


async def deliver_webapp_analysis_to_telegram(user_id: int, market_url: str, raw_result: Dict[str, Any], lang: str = "en") -> Dict[str, Any]:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        return {"attempted": False, "sent": False, "error": "bot_not_configured"}

    report = build_webapp_analysis_report(raw_result=raw_result or {}, market_url=market_url, lang=lang)
    text = (
        f"🔍 DeepAlpha WebApp analysis\n\n"
        f"📌 {report.get('question', '')}\n"
        f"🎯 {report.get('display_prediction', '')}\n"
        f"📊 {report.get('market_probability', '')}\n"
        f"🧠 {report.get('confidence', '')}\n"
        f"🏷 {report.get('category', '')}\n\n"
        f"✅ {report.get('conclusion', '')}"
    ).strip()

    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id=user_id, text=text)
        return {"attempted": True, "sent": True, "error": ""}
    except Exception:
        return {"attempted": True, "sent": False, "error": "delivery_failed"}
    finally:
        try:
            session = await bot.get_session()
            await session.close()
        except Exception:
            pass
