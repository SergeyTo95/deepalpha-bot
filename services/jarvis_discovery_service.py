"""Public Telegram discovery for Jarvis team /post.

The service intentionally uses only public web surfaces:
- public Telegram message pages (https://t.me/<slug>/<id>)
- public Telegram channel previews (https://t.me/s/<slug>/<id>)
- optional public web search/index pages that return Telegram links

It never logs into Telegram, never touches private chats/DMs, and never fabricates
sources. If public discovery is unavailable, callers receive a natural empty state.
"""

from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

TELEGRAM_HOSTS = {"t.me", "telegram.me"}
DEFAULT_QUERIES = (
    "Polymarket",
    "Polymarket odds",
    "Polymarket YES NO",
    "Polymarket Trump",
    "Polymarket election",
    "Polymarket market",
    "Polymarket prediction",
)
POLYMARKET_TERMS = ("polymarket", "poly.market", "polymarket.com")
DISCUSSION_TERMS = (
    "odds", "yes", "no", "probability", "вероят", "шанс", "market", "рынок",
    "election", "trump", "biden", "crypto", "bitcoin", "btc", "eth", "mispriced",
    "outcome", "resolve", "resolution", "спор", "исход", "став", "prediction",
)
EMOTIONAL_TERMS = (
    "wrong", "fake", "lol", "scam", "rigged", "crazy", "insane", "dumb", "cope",
    "wtf", "bullish", "bearish", "agree", "disagree", "спор", "бред", "лол",
    "скам", "невер", "ошиб", "жесть", "имхо", "соглас", "против",
)
SPAM_TERMS = ("airdrop", "giveaway", "referral", "promo code", "free money", "join now")


@dataclass(frozen=True)
class TelegramDiscovery:
    title: str
    url: str
    freshness: str
    why: str
    reply: str
    score: int
    text: str = ""
    created_at: Optional[datetime] = None
    freshness_verified: bool = False


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def _fetch(url: str, timeout: int = 12) -> Tuple[str, dict]:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DeepAlphaJarvis/1.0; public Telegram discovery)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,ru;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read(1_500_000)
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, "ignore"), dict(resp.headers.items())


def _clean_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _is_public_telegram_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().split(":", 1)[0]
    if host not in TELEGRAM_HOSTS:
        return False
    path = parsed.path.strip("/")
    if not path or path.startswith(("c/", "+", "joinchat/", "addstickers/", "share/")):
        return False
    return bool(re.match(r"^[A-Za-z0-9_]{4,}/\d+", path) or re.match(r"^s/[A-Za-z0-9_]{4,}(/\d+)?", path))


def _normalize_telegram_url(url: str) -> Optional[str]:
    url = html.unescape(unquote(url or "")).strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("https://t.me/", "http://t.me/", "https://telegram.me/", "http://telegram.me/")):
        return None
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    if parts and parts[0] == "s":
        parts = parts[1:]
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    if parts[0] in {"c", "joinchat"} or parts[0].startswith("+"):
        return None
    normalized = f"https://t.me/{parts[0]}/{parts[1]}"
    return normalized if _is_public_telegram_url(normalized) else None


def _extract_search_links(page: str) -> List[str]:
    links: List[str] = []
    for href in re.findall(r"href=[\"']([^\"']+)[\"']", page or "", flags=re.IGNORECASE):
        candidate = html.unescape(href)
        if "uddg=" in candidate:
            qs = parse_qs(urlparse(candidate).query)
            if qs.get("uddg"):
                candidate = qs["uddg"][0]
        elif "/url?" in candidate and "q=" in candidate:
            qs = parse_qs(urlparse(candidate).query)
            if qs.get("q"):
                candidate = qs["q"][0]
        normalized = _normalize_telegram_url(candidate)
        if normalized and normalized not in links:
            links.append(normalized)
    for raw in re.findall(r"https?://(?:t\.me|telegram\.me)/[A-Za-z0-9_/]+", page or ""):
        normalized = _normalize_telegram_url(raw)
        if normalized and normalized not in links:
            links.append(normalized)
    return links


def _search_urls(query: str) -> Sequence[str]:
    encoded = quote_plus(f"site:t.me {query}")
    return (
        f"https://duckduckgo.com/html/?q={encoded}",
        f"https://www.bing.com/search?q={encoded}",
    )


def _search_public_web(query: str) -> List[str]:
    results: List[str] = []
    for url in _search_urls(query):
        try:
            page, _ = _fetch(url, timeout=10)
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
        for link in _extract_search_links(page):
            if link not in results:
                results.append(link)
        if len(results) >= 8:
            break
    return results


def _candidate_urls_from_env() -> List[str]:
    raw = os.getenv("JARVIS_TELEGRAM_SEED_URLS", "")
    urls: List[str] = []
    for item in re.split(r"[\s,]+", raw):
        normalized = _normalize_telegram_url(item)
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def _preview_url(url: str) -> str:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[1].isdigit():
        return f"https://t.me/s/{parts[0]}/{parts[1]}"
    return url


def _parse_telegram_message(url: str) -> Optional[Tuple[str, Optional[datetime]]]:
    pages = [_preview_url(url), url]
    for page_url in pages:
        try:
            page, _headers = _fetch(page_url, timeout=12)
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
        message_blocks = re.findall(
            r"<div class=\"tgme_widget_message[^\"]*\".*?</div>\s*</div>\s*</div>",
            page,
            flags=re.IGNORECASE | re.DOTALL,
        ) or [page]
        for block in message_blocks:
            if "Polymarket" not in block and "polymarket" not in block.lower():
                continue
            text_match = re.search(
                r"<div class=\"tgme_widget_message_text[^\"]*\"[^>]*>(.*?)</div>",
                block,
                flags=re.IGNORECASE | re.DOTALL,
            )
            text = _clean_text(text_match.group(1) if text_match else block)
            if not any(term in text.lower() for term in POLYMARKET_TERMS):
                continue
            dt = None
            time_match = re.search(r"<time[^>]+datetime=[\"']([^\"']+)[\"']", block, flags=re.IGNORECASE)
            if time_match:
                raw_dt = time_match.group(1).replace("Z", "+00:00")
                try:
                    dt = datetime.fromisoformat(raw_dt).astimezone(timezone.utc)
                except ValueError:
                    dt = None
            return text, dt
    return None


def _freshness(created_at: Optional[datetime], now: datetime) -> Tuple[str, bool, bool]:
    if not created_at:
        return "Свежесть не подтверждена", False, True
    delta = now - created_at
    hours = max(0, int(delta.total_seconds() // 3600))
    if hours > 48:
        return f"~{hours} часов назад", True, False
    if hours < 1:
        return "меньше часа назад", True, True
    if hours < 24:
        return f"~{hours} часов назад", True, True
    return f"~{hours // 24} дн. назад", True, True


def _score_text(text: str, verified_fresh: bool) -> int:
    lower = (text or "").lower()
    score = 20 if any(term in lower for term in POLYMARKET_TERMS) else 0
    score += sum(4 for term in DISCUSSION_TERMS if term in lower)
    score += sum(3 for term in EMOTIONAL_TERMS if term in lower)
    score -= sum(8 for term in SPAM_TERMS if term in lower)
    if "?" in text:
        score += 4
    if re.search(r"\b(yes|no)\b", lower):
        score += 8
    if re.search(r"\b\d{1,3}\s?%", lower):
        score += 6
    if verified_fresh:
        score += 12
    return score


def _topic_from_text(text: str) -> str:
    compact = re.sub(r"https?://\S+", "", text or "").strip()
    words = compact.split()
    if not words:
        return "Polymarket discussion"
    topic = " ".join(words[:9])
    return topic[:80].rstrip(".,;:!?")


def _why_from_text(text: str) -> str:
    lower = (text or "").lower()
    if re.search(r"\b(yes|no)\b", lower) and ("odds" in lower or "%" in lower):
        return "Спорят про YES/NO и вероятность — хороший вход для аккуратного комментария про odds."
    if any(term in lower for term in ("trump", "biden", "election", "выбор")):
        return "Политический рынок обычно вызывает эмоции и разногласия по вероятности исхода."
    if any(term in lower for term in ("mispriced", "wrong", "ошиб", "невер")):
        return "Есть намёк на mispricing — можно спокойно сравнить мнение толпы и реальную probability."
    if any(term in lower for term in ("btc", "bitcoin", "crypto", "eth")):
        return "Крипто-тема + Polymarket часто даёт живой спор о sentiment и вероятности."
    return "Упоминают Polymarket в контексте рынка/прогноза — можно войти без промо и обсудить probability."


def _reply_from_text(text: str) -> str:
    lower = (text or "").lower()
    if re.search(r"\b(yes|no)\b", lower):
        return "Рынок уже слишком уверенно смотрит в одну сторону. Интереснее сравнить эти odds с независимой probability, а не только с эмоциями в треде."
    if "mispriced" in lower or "wrong" in lower or "ошиб" in lower:
        return "Главное не путать популярное мнение и реальную вероятность события. Иногда рынок выглядит mispriced именно из-за перегретого sentiment."
    if any(term in lower for term in ("trump", "biden", "election")):
        return "В политических рынках sentiment часто бежит быстрее фактов. Я бы смотрел не только на текущие odds, но и на то, что должно реально изменить probability."
    return "Интересно сравнить market odds с независимой probability моделью. Если разрыв слабый, возможно, рынок уже всё учёл."


def _format_discoveries(items: Sequence[TelegramDiscovery]) -> str:
    if not items:
        return "Сейчас свежих Telegram-обсуждений почти нет. Лучше проверить позже или поискать спорные рынки вручную."
    blocks: List[str] = []
    for item in items:
        kind = "комментарии" if "discussion" in item.url.lower() or "comments" in item.url.lower() else "обсуждение"
        blocks.append(
            f"🧠 Нашёл {kind}\n\n"
            f"Тема:\n{item.title}\n\n"
            f"Где:\n{item.url}\n\n"
            f"Свежесть:\n{item.freshness}\n\n"
            f"Почему это интересно:\n{item.why}\n\n"
            f"Что можно ответить:\n\"{item.reply}\""
        )
    return "\n\n━━━━━━━━\n\n".join(blocks)


def _build_queries(user_text: str = "") -> List[str]:
    queries = list(DEFAULT_QUERIES)
    cleaned = re.sub(r"[^\w\s%/-]", " ", user_text or "", flags=re.UNICODE).strip()
    if cleaned:
        queries.insert(0, f"Polymarket {cleaned[:80]}")
    return queries


def discover_telegram_polymarket(user_text: str = "", limit: int = 5) -> List[TelegramDiscovery]:
    """Return verified public Telegram discoveries, never fabricated links."""
    now = datetime.now(timezone.utc)
    max_candidates = max(8, _env_int("JARVIS_TELEGRAM_DISCOVERY_MAX_CANDIDATES", 30))
    candidates: List[str] = []
    for url in _candidate_urls_from_env():
        candidates.append(url)
    for query in _build_queries(user_text):
        for url in _search_public_web(query):
            if url not in candidates:
                candidates.append(url)
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break
        time.sleep(0.15)

    discoveries: List[TelegramDiscovery] = []
    seen_urls = set()
    seen_texts = set()
    for url in candidates:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        parsed = _parse_telegram_message(url)
        if not parsed:
            continue
        text, created_at = parsed
        normalized_text_key = re.sub(r"\W+", "", text.lower())[:220]
        if normalized_text_key in seen_texts:
            continue
        seen_texts.add(normalized_text_key)
        freshness, verified, allowed = _freshness(created_at, now)
        if not allowed:
            continue
        score = _score_text(text, verified)
        if score < 20:
            continue
        discoveries.append(
            TelegramDiscovery(
                title=_topic_from_text(text),
                url=url,
                freshness=freshness,
                why=_why_from_text(text),
                reply=_reply_from_text(text),
                score=score,
                text=text,
                created_at=created_at,
                freshness_verified=verified,
            )
        )

    discoveries.sort(key=lambda item: (item.freshness_verified, item.score, item.created_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return discoveries[: max(1, min(limit, 5))]


async def render_telegram_discovery(user_text: str = "", limit: int = 5) -> str:
    import asyncio

    try:
        items = await asyncio.wait_for(asyncio.to_thread(discover_telegram_polymarket, user_text, limit), timeout=35)
    except Exception:
        return "Сейчас свежих Telegram-обсуждений почти нет. Лучше проверить позже или поискать спорные рынки вручную."
    return _format_discoveries(items)
