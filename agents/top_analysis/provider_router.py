import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)


class TopAnalysisProviderRouter:
    ROLE_CONFIG = {
        "research_llm": {
            "provider_env": "TOP_ANALYSIS_RESEARCH_PROVIDER",
            "model_env": "TOP_ANALYSIS_RESEARCH_MODEL",
            "key_env": "GEMINI_API_KEY",
            "default_provider": "gemini",
            "default_model": "gemini-2.5-flash",
        },
        "social_llm": {
            "provider_env": "TOP_ANALYSIS_SOCIAL_PROVIDER",
            "model_env": "TOP_ANALYSIS_SOCIAL_MODEL",
            "key_env": "XAI_API_KEY",
            "default_provider": "xai",
            "default_model": "grok-4",
        },
        "audit_llm": {
            "provider_env": "TOP_ANALYSIS_AUDIT_PROVIDER",
            "model_env": "TOP_ANALYSIS_AUDIT_MODEL",
            "key_env": "ANTHROPIC_API_KEY",
            "default_provider": "anthropic",
            "default_model": "claude-sonnet-4-5",
        },
        "chief_llm": {
            "provider_env": "TOP_ANALYSIS_CHIEF_PROVIDER",
            "model_env": "TOP_ANALYSIS_CHIEF_MODEL",
            "key_env": "OPENAI_API_KEY",
            "default_provider": "openai",
            "default_model": "gpt-5.5-thinking",
        },
    }

    def _timeout_sec(self) -> int:
        raw = os.getenv("TOP_ANALYSIS_TIMEOUT_SEC", "120")
        try:
            return max(5, int(raw))
        except Exception:
            return 120

    def _safe_response(self, provider_key: str, provider: str, model: str, status: str, content: str = "", parsed: Optional[Dict[str, Any]] = None, error: str = "") -> Dict[str, Any]:
        return {
            "status": status,
            "provider_key": provider_key,
            "provider": provider,
            "model": model,
            "content": content,
            "json": parsed or {},
            "error": error,
        }

    def is_execution_ready(self) -> bool:
        for role, cfg in self.ROLE_CONFIG.items():
            provider = os.getenv(cfg["provider_env"], cfg["default_provider"]).strip().lower()
            model = os.getenv(cfg["model_env"], cfg["default_model"]).strip()
            api_key = os.getenv(cfg["key_env"], "").strip()
            if role not in self.ROLE_CONFIG or not provider or not model or not api_key:
                return False
        return True

    def route(self, provider_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.time()
        cfg = self.ROLE_CONFIG.get(provider_key)
        if not cfg:
            return self._safe_response(provider_key, "", "", "error", error="unsupported_provider_key")
        provider = os.getenv(cfg["provider_env"], cfg["default_provider"]).strip().lower()
        model = os.getenv(cfg["model_env"], cfg["default_model"]).strip()
        api_key = os.getenv(cfg["key_env"], "").strip()
        if not provider or not model or not api_key:
            return self._safe_response(provider_key, provider, model, "error", error="provider_configuration_missing")

        try:
            if provider == "gemini":
                result = self._call_gemini(provider_key, model, api_key, payload)
            elif provider == "openai":
                result = self._call_openai(provider_key, model, api_key, payload)
            elif provider == "anthropic":
                result = self._call_anthropic(provider_key, model, api_key, payload)
            elif provider == "xai":
                result = self._call_xai(provider_key, model, api_key, payload)
            else:
                result = self._safe_response(provider_key, provider, model, "error", error="unsupported_provider")
        except Exception:
            result = self._safe_response(provider_key, provider, model, "error", error="provider_call_exception")

        elapsed_ms = int((time.time() - started) * 1000)
        logger.info("top_analysis_route provider_key=%s provider=%s model=%s status=%s elapsed_ms=%s", provider_key, provider, model, result.get("status"), elapsed_ms)
        return result

    def _extract_and_parse(self, text: str) -> Dict[str, Any]:
        clean = (text or "").strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        if clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()
        try:
            parsed = json.loads(clean)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _http_json(self, url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
        timeout = self._timeout_sec()
        if requests is not None:
            response = requests.post(url, headers=headers, json=body, timeout=timeout)
            return {"status_code": response.status_code, "text": response.text}
        req = urllib.request.Request(url=url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return {"status_code": resp.getcode(), "text": resp.read().decode("utf-8")}
        except urllib.error.HTTPError as exc:
            return {"status_code": exc.code, "text": exc.read().decode("utf-8")}

    def _call_gemini(self, provider_key: str, model: str, api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = payload.get("prompt", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        raw = self._http_json(url, {"Content-Type": "application/json"}, {"contents": [{"parts": [{"text": prompt}]}]})
        if raw["status_code"] >= 300:
            return self._safe_response(provider_key, "gemini", model, "error", error=f"http_{raw['status_code']}")
        data = json.loads(raw["text"] or "{}")
        text = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text", "")
        if not (text or "").strip():
            return self._safe_response(provider_key, "gemini", model, "error", error="empty_provider_response")
        return self._safe_response(provider_key, "gemini", model, "ok", content=text, parsed=self._extract_and_parse(text))

    def _call_openai(self, provider_key: str, model: str, api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = payload.get("prompt", "")
        raw = self._http_json("https://api.openai.com/v1/responses", {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, {"model": model, "input": prompt})
        if raw["status_code"] >= 300:
            return self._safe_response(provider_key, "openai", model, "error", error=f"http_{raw['status_code']}")
        data = json.loads(raw["text"] or "{}")
        text = data.get("output_text", "")
        if not text:
            output = data.get("output") or []
            for item in output:
                for content_item in item.get("content", []):
                    t = content_item.get("text") or content_item.get("content")
                    if isinstance(t, str) and t.strip():
                        text = t
                        break
                if text:
                    break
        if not text:
            for key in ("text", "content", "message"):
                t = data.get(key)
                if isinstance(t, str) and t.strip():
                    text = t
                    break
        if not (text or "").strip():
            return self._safe_response(provider_key, "openai", model, "error", error="empty_provider_response")
        return self._safe_response(provider_key, "openai", model, "ok", content=text, parsed=self._extract_and_parse(text))

    def _call_anthropic(self, provider_key: str, model: str, api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = payload.get("prompt", "")
        body = {"model": model, "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]}
        raw = self._http_json("https://api.anthropic.com/v1/messages", {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, body)
        if raw["status_code"] >= 300:
            return self._safe_response(provider_key, "anthropic", model, "error", error=f"http_{raw['status_code']}")
        data = json.loads(raw["text"] or "{}")
        items = data.get("content") or []
        text = items[0].get("text", "") if items and isinstance(items[0], dict) else ""
        if not (text or "").strip():
            return self._safe_response(provider_key, "anthropic", model, "error", error="empty_provider_response")
        return self._safe_response(provider_key, "anthropic", model, "ok", content=text, parsed=self._extract_and_parse(text))

    def _call_xai(self, provider_key: str, model: str, api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = payload.get("prompt", "")
        body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        raw = self._http_json("https://api.x.ai/v1/chat/completions", {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, body)
        if raw["status_code"] >= 300:
            return self._safe_response(provider_key, "xai", model, "error", error=f"http_{raw['status_code']}")
        data = json.loads(raw["text"] or "{}")
        choices = data.get("choices") or []
        text = (((choices[0] or {}).get("message") or {}).get("content", "")) if choices else ""
        if not (text or "").strip():
            return self._safe_response(provider_key, "xai", model, "error", error="empty_provider_response")
        return self._safe_response(provider_key, "xai", model, "ok", content=text, parsed=self._extract_and_parse(text))
