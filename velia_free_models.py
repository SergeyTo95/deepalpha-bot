"""Opt-in, pinned free API tiers. No model supplied by a client is an upstream ID."""
import os
import re
from velia_quantum_provider import QuantumConfig, ZAI_URL

TIERS = {"flash": ("zai", "glm-4.5-flash", 2048),
         "quantum": ("zai", "glm-4.7-flash", 4096),
         "pro": ("zai", "glm-4.7-flash", 8192)}

# Only self-contained social greetings. Confirmations and continuations may
# refer to a hard task in history and must keep PRO's reasoning enabled.
_SOCIAL_GREETING = re.compile(
    r"(?:привет|здравствуй(?:те)?|доброе утро|добрый (?:день|вечер)|как (?:ты|дела)|"
    r"(?:привет[ ,!.]*|hello[ ,!.]*|hi[ ,!.]*)?(?:как дела|how are you)|"
    r"hello|hi|hey|good (?:morning|evening)|merhaba|selam|nasılsın|nasılsınız|"
    r"hola|bonjour|salut|ciao|hallo|olá|你好|您好|こんにちは|안녕하세요|مرحبا|أهلا|नमस्ते)"
    r"[\s.!?！。？]*", re.IGNORECASE)


def fast_social_turn(messages):
    if (not isinstance(messages, list) or not messages or any(not isinstance(m, dict) for m in messages)
            or messages[-1].get("role") != "user"):
        return False
    if any(m.get("attachments") or m.get("role") == "tool" or any(marker in str(m.get("content", ""))
            for marker in ("[BEGIN_ATTACHMENT", "ATTACHMENT_DATA_UNTRUSTED", "ATTACHMENT DATA — UNTRUSTED")) for m in messages):
        return False
    content = messages[-1].get("content")
    return isinstance(content, str) and len(content) <= 120 and bool(_SOCIAL_GREETING.fullmatch(content.strip()))


def enabled():
    return os.getenv("VELIA_FREE_API_TIERS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def configuration(mode, *, voice=False):
    if not enabled() or mode not in TIERS:
        return None
    provider, model, limit = TIERS[mode]
    if provider == "zai":
        key = os.getenv("VELIA_ZAI_API_KEY", "").strip()
        # Reuse only a key already assigned to the exact official Z.ai route.
        if not key and os.getenv("VELIA_QUANTUM_PROVIDER", "").strip().lower() == "zai" and os.getenv("VELIA_QUANTUM_BASE_URL", ZAI_URL).rstrip("/") == ZAI_URL:
            key = os.getenv("VELIA_QUANTUM_API_KEY", "").strip()
        url = ZAI_URL
    if not key or "${{" in key or any(c in key for c in "\r\n"):
        return None
    return QuantumConfig(provider, url, model, key,
                         min(limit, 4096 if mode == "pro" else 1024) if voice else limit,
                         thinking_enabled=mode == "pro")


def public_capability():
    return {"chat_free_api_tiers": enabled(),
            "chat_pro_free": bool(configuration("pro")),
            "chat_flash": bool(configuration("flash")),
            "chat_quantum": bool(configuration("quantum"))}


def error_code(code, mode):
    return str(code).replace("quantum_", mode + "_", 1)


VOICE_PROVIDERS = frozenset({'voice_groq', 'voice_cloudflare', 'voice_openrouter'})


def _on(name):
    return os.getenv(name, '').strip().lower() in {'1','true','yes','on'}


def voice_reserves_enabled():
    return enabled() and _on('VELIA_VOICE_RESERVES_ENABLED')


def _key(name):
    value = os.getenv(name, '').strip()
    return value if value and '${{' not in value and not any(c in value for c in '\r\n') else ''


def voice_reserves(mode='flash'):
    """Fixed endpoints/models. Account-plan attestations are not billing API proofs."""
    if not voice_reserves_enabled() or mode not in TIERS:
        return ()
    routes = []
    key = _key('GROQ_API_KEY')
    if key and _on('VELIA_VOICE_GROQ_FREE_ACCOUNT'):
        routes.append(QuantumConfig('voice_groq','https://api.groq.com/openai/v1','openai/gpt-oss-20b',key,1024))
    key, account = _key('VELIA_VOICE_CF_API_TOKEN'), os.getenv('VELIA_VOICE_CF_ACCOUNT_ID','').strip()
    if key and re.fullmatch(r'[a-fA-F0-9]{32}',account) and _on('VELIA_VOICE_CF_FREE_ACCOUNT'):
        routes.append(QuantumConfig('voice_cloudflare',f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1',
            '@cf/qwen/qwen3-30b-a3b-fp8',key,1024))
    key = _key('VELIA_VOICE_OPENROUTER_API_KEY')
    if key:
        routes.append(QuantumConfig('voice_openrouter','https://openrouter.ai/api/v1','openrouter/free',key,1024))
    return tuple(routes)


def require_voice_reserve(config):
    from velia_quantum_provider import QuantumError
    if config not in voice_reserves():
        raise QuantumError('quantum_free_guard_required',503)


def voice_payload(config, result, tools, tool_choice):
    from velia_quantum_provider import QuantumError
    require_voice_reserve(config)
    if (tools and any(t.get('type')!='function' for t in tools)) or tool_choice not in (None,'auto','required'):
        raise QuantumError('quantum_free_guard_required',503)
    if config.provider=='voice_groq':
        result['max_completion_tokens']=result.pop('max_tokens')
        result.update(reasoning_effort='low',include_reasoning=False)
    if config.provider=='voice_cloudflare':
        result['options']={'rejectIfBusy':True}
    if config.provider=='voice_openrouter':
        # The free router on its own cannot fall back to a paid model. No plugins,
        # built-in tools, paid model arrays, BYOK or client-supplied routing fields.
        result['provider']={'max_price':{'prompt':0,'completion':0},'data_collection':'deny'}
    return result


def verify_voice_key(client, config):
    from velia_quantum_provider import QuantumError, require_zero_spend_key
    import time
    require_voice_reserve(config)
    if config.provider!='voice_openrouter':
        return
    try:
        deadline=time.monotonic()+5
        with client.get('https://openrouter.ai/api/v1/key',headers={'Authorization':'Bearer '+config.api_key,'Cache-Control':'no-cache'},
                timeout=(2,3),stream=True,allow_redirects=False) as response:
            if response.status_code!=200:raise ValueError
            raw=bytearray()
            for part in response.iter_content(2048):
                raw.extend(part)
                if len(raw)>8192 or time.monotonic()>deadline:raise ValueError
            require_zero_spend_key(raw)
    except Exception:
        raise QuantumError('quantum_free_guard_required',503) from None


async def verify_voice_key_async(client, config):
    from velia_quantum_provider import QuantumError, require_zero_spend_key
    import asyncio
    require_voice_reserve(config)
    if config.provider!='voice_openrouter':
        return
    async def read():
        async with client.get('https://openrouter.ai/api/v1/key',headers={'Authorization':'Bearer '+config.api_key,'Cache-Control':'no-cache'},
                allow_redirects=False) as response:
            if response.status!=200:raise ValueError
            raw=bytearray()
            async for part in response.content.iter_chunked(2048):
                raw.extend(part)
                if len(raw)>8192:raise ValueError
            require_zero_spend_key(raw)
    try:
        await asyncio.wait_for(read(),timeout=5)
    except Exception:
        raise QuantumError('quantum_free_guard_required',503) from None
