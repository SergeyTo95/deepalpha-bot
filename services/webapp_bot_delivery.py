import os
from typing import Any, Dict, List

from telegram import Bot


def _split_chunks(text: str, limit: int = 3500) -> List[str]:
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    lines = text.split("\n")
    chunks: List[str] = []
    cur = ""
    for line in lines:
        extra = ("\n" if cur else "") + line
        if len(cur) + len(extra) <= limit:
            cur += extra
        else:
            if cur:
                chunks.append(cur)
            if len(line) <= limit:
                cur = line
            else:
                for i in range(0, len(line), limit):
                    chunks.append(line[i:i + limit])
                cur = ""
    if cur:
        chunks.append(cur)
    return chunks


async def deliver_webapp_analysis_to_telegram(user_id: int, report: Dict[str, Any], lang: str) -> Dict[str, Any]:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        return {"attempted": False, "sent": False, "error": "bot_token_missing"}

    text = str((report or {}).get("telegram_text") or "").strip()
    if not text:
        return {"attempted": False, "sent": False, "error": "empty_text"}

    try:
        bot = Bot(token=token)
        chunks = _split_chunks(text)
        if not chunks:
            return {"attempted": False, "sent": False, "error": "empty_text"}
        for chunk in chunks:
            await bot.send_message(chat_id=int(user_id), text=chunk)
        return {"attempted": True, "sent": True}
    except Exception as e:
        safe_err = f"{e.__class__.__name__}: {str(e)[:180]}"
        print(f"webapp telegram delivery error: {safe_err}")
        return {"attempted": True, "sent": False, "error": safe_err}
