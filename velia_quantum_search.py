"""Bounded read-only search using the existing configured provider credentials."""
import json
import os
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
from urllib.parse import urlsplit
from velia_quantum_provider import QuantumError

HEALTH_DOMAINS = ("nhs.uk", "cdc.gov", "nhlbi.nih.gov", "niddk.nih.gov", "nice.org.uk", "who.int")


def configuration():
    for prefix in ("VELIA_WEB_SEARCH_", "WEB_SEARCH_"):
        provider, key = os.getenv(prefix + "PROVIDER", "").strip().lower(), os.getenv(prefix + "API_KEY", "").strip()
        if provider in {"tavily", "serper", "brave", "bing"} and key:
            return provider, key
    return None


def public_url(value):
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password
                or parsed.port not in {None, 80, 443} or host.endswith((".local", ".internal", ".localhost"))
                or host == "localhost" or "%" in host):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host or all(c.isdigit() or c == "." for c in host) or host.startswith("0x"):
                return None
        return value
    except ValueError:
        return None


class PlainText(HTMLParser):
    def __init__(self, value, limit):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(str(value or "")[:16000])
        self.value = " ".join(" ".join(self.parts).split())[:limit]
    def handle_data(self, data):
        self.parts.append(data)


def search(query, *, scope="general"):
    import requests
    configured = configuration()
    if (configured is None or not isinstance(query, str) or not 1 <= len(query.strip()) <= 400
            or scope not in {"general", "official_health"}):
        raise QuantumError("quantum_web_search_unavailable", 503)
    provider, key = configured
    query = " ".join(query.split()[:50])
    headers = {"Accept": "application/json", "User-Agent": "VELIA-Quantum/1.0"}
    method, kwargs = "POST", {}
    if provider == "tavily":
        url = "https://api.tavily.com/search"
        kwargs["json"] = {"api_key": key, "query": query, "search_depth": "basic", "max_results": 6,
            "include_answer": False, "include_raw_content": False, "auto_parameters": False}
        if scope == "official_health":
            kwargs["json"].update(include_domains=list(HEALTH_DOMAINS), include_domains_mode="restrict")
    else:
        if scope == "official_health":
            query += " (" + " OR ".join("site:" + d for d in HEALTH_DOMAINS) + ")"
        if provider == "serper":
            url = "https://google.serper.dev/search"
            headers["X-API-KEY"] = key
            kwargs["json"] = {"q": query, "num": 6}
        elif provider == "brave":
            method, url = "GET", "https://api.search.brave.com/res/v1/web/search"
            headers["X-Subscription-Token"] = key
            kwargs["params"] = {"q": query, "count": 6, "text_decorations": "false"}
        else:
            method, url = "GET", "https://api.bing.microsoft.com/v7.0/search"
            headers["Ocp-Apim-Subscription-Key"] = key
            kwargs["params"] = {"q": query, "count": 6}
    try:
        deadline = time.monotonic() + 15
        with requests.Session() as client:
            client.trust_env = False
            with client.request(method, url, headers=headers, timeout=(3, 10), stream=True,
                    allow_redirects=False, **kwargs) as response:
                if response.status_code != 200 or "json" not in response.headers.get("Content-Type", ""):
                    raise ValueError("search_rejected")
                raw = bytearray()
                for chunk in response.iter_content(1):
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024 or time.monotonic() > deadline:
                        raise ValueError("search_too_large")
                body = json.loads(raw)
        rows = (body.get("results") if provider == "tavily" else body.get("organic") if provider == "serper"
            else body.get("web", {}).get("results") if provider == "brave" else body.get("webPages", {}).get("value"))
        selected, seen = [], set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = public_url(row.get("url") or row.get("link"))
            title = PlainText(row.get("title") or row.get("name"), 160).value
            snippet = PlainText(row.get("content") or row.get("snippet") or row.get("description"), 900).value
            host = (urlsplit(url).hostname or "").lower().rstrip(".") if url else ""
            if (not url or not title or not snippet or url in seen or scope == "official_health"
                    and not any(host == d or host.endswith("." + d) for d in HEALTH_DOMAINS)):
                continue
            seen.add(url)
            selected.append({"title": title, "url": url, "snippet": snippet})
            if len(selected) == 3:
                break
        if not selected:
            raise ValueError("search_empty")
        return {"results": selected, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    except (requests.RequestException, OSError, ValueError, TypeError, AttributeError):
        raise QuantumError("quantum_web_search_unavailable", 503) from None
