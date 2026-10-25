"""Quantum may interpret/search even when every Flash route is unavailable."""
import asyncio

import pytest

from desktop.guest_store import GuestStore
from test_velia_web_chat import fixture, login, headers


@pytest.mark.parametrize("action", ["direct", "search"])
@pytest.mark.parametrize("saved", [True, False])
def test_quantum_owns_interpretation_and_search_with_flash_unavailable(monkeypatch, tmp_path, action, saved):
    def forbidden(*args, **kwargs):
        pytest.fail("Quantum reached Flash or Decision routing")
    monkeypatch.setattr("desktop.web_search.understand", forbidden)
    monkeypatch.setattr("desktop.decision_router.try_direct", forbidden)
    async def run():
        plan={"action":action,"quote":"","candidate":"","query":"Python release" if action=="search" else "",
            "source_scope":"general","task_query":"Python release" if action=="search" else "","context":[]}
        async with fixture(monkeypatch, with_search=True, quantum_api=True, without_flash=True,
                features={"chat_quantum":True}, intent=plan,
                guest_store=GuestStore(sqlite_path=tmp_path/"quantum-own.db")) as (server, client, state):
            cookie, _, _=await login(server,client)
            question="Найди текущую версию Python" if action=="search" else "Объясни разницу между списком и множеством."
            if saved:
                async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Quantum"}) as response:
                    cid=(await response.json())["conversation"]["id"]
                endpoint=f"/web-api/v1/conversations/{cid}/messages/stream"
                body={"content":question,"model":"velia-quantum","idempotency_key":"quantum-own-01"}
            else:
                endpoint="/web-api/v1/chat/completions"
                body={"model":"velia-quantum","stream":True,"messages":[{"role":"user","content":question}]}
            async with client.post(server.make_url(endpoint),headers=headers(cookie),json=body) as response:
                wire=await response.text()
                assert response.status==200 and "[DONE]" in wire
                assert "fixture-quantum-key" not in wire and "private-thought" not in wire
                assert ("https://www.python.org/downloads/" in wire)==(action=="search")
            assert len(state["quantum_intent_payloads"])==1
            assert state["intent_payloads"]==state["forbidden_model_calls"]==state["review_payloads"]==[]
            assert len(state["search_queries"])==(1 if action=="search" else 0)
            request=state["quantum_intent_payloads"][0]
            assert request["model"]=="owned-quantum" and request["tool_choice"]=="required"
            assert "chat_template_kwargs" not in request and "thinking_budget_tokens" not in request
            if saved: assert state["account_calls"][-1]["chat_mode"]=="quantum"
    asyncio.run(run())


@pytest.mark.parametrize("status,code", [(429,"quantum_quota_exhausted"),(402,"quantum_access_required"),(503,"quantum_provider_error")])
def test_quantum_preparation_failure_never_falls_back_or_generates(monkeypatch, tmp_path, status, code):
    async def run():
        async with fixture(monkeypatch, with_search=True, quantum_api=True, without_flash=True,
                features={"chat_quantum":True}, intent_status=status,
                guest_store=GuestStore(sqlite_path=tmp_path/"quantum-error.db")) as (server, client, state):
            cookie, _, _=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Quantum"}) as response:
                cid=(await response.json())["conversation"]["id"]
            async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"),headers=headers(cookie),
                    json={"content":"Объясни разницу между списком и множеством.","model":"velia-quantum","idempotency_key":"quantum-error-01"}) as response:
                wire=await response.text()
                assert code in wire and "[DONE]" not in wire and "fixture failure" not in wire
            assert len(state["quantum_intent_payloads"])==1
            assert state["intent_payloads"]==state["forbidden_model_calls"]==state["account_calls"]==[]
    asyncio.run(run())


@pytest.mark.parametrize("case", ["redirect", "wrong_tool", "oversized", "truncated", "malformed"])
def test_quantum_plan_rejects_invalid_provider_results_without_retry(monkeypatch, case):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from desktop.quantum_intent import understand_quantum
    from velia_quantum_provider import QuantumError
    async def run():
        calls=[]
        async def provider(request):
            calls.append(request.path)
            if case=="redirect":
                raise web.HTTPTemporaryRedirect(location="/followed")
            if case in {"oversized","malformed"}:
                return web.Response(body=b" "*65537 if case=="oversized" else b"{",content_type="application/json")
            return web.json_response({"choices":[{"message":{"tool_calls":[{"function":{
                "name":"not_allowed" if case=="wrong_tool" else "understand_request",
                "arguments":'{"action":"direct","quote":"","query":""}'}}]},
                "finish_reason":"length" if case=="truncated" else "tool_calls"}]})
        app=web.Application()
        app.router.add_post("/v1/chat/completions",provider)
        app.router.add_route("*","/followed",provider)
        async with TestServer(app) as server:
            for key,value in {"VELIA_QUANTUM_ENABLED":"true","VELIA_QUANTUM_PROVIDER":"self_hosted",
                    "VELIA_QUANTUM_BASE_URL":str(server.make_url("/v1")),"VELIA_QUANTUM_MODEL":"owned-quantum",
                    "VELIA_QUANTUM_API_KEY":"dedicated-test-key"}.items():
                monkeypatch.setenv(key,value)
            with pytest.raises(QuantumError) as error:
                await understand_quantum([{"role":"user","content":"Объясни двоичный поиск."}])
            assert error.value.code==("quantum_provider_error" if case=="redirect" else "quantum_invalid_response")
        assert calls==["/v1/chat/completions"]
    asyncio.run(run())


def test_quantum_medical_search_preserves_official_sources_without_flash(monkeypatch,tmp_path):
    async def run():
        plan={"action":"search","quote":"","candidate":"","query":"healthy weight loss with asthma",
            "source_scope":"official_health","task_query":"healthy weight loss advice",
            "context":[{"quote":"астма","kind":"condition","status":"stated"}]}
        async with fixture(monkeypatch,with_search=True,quantum_api=True,without_flash=True,
                features={"chat_quantum":True},intent=plan,
                search_response={"results":[{"title":"NHS","url":"https://www.nhs.uk/live-well/healthy-weight/","content":"Gradual physical activity."},
                    {"title":"Impostor","url":"https://nhs.uk.attacker.example/diet","content":"Unverified."}]},
                guest_store=GuestStore(sqlite_path=tmp_path/"quantum-health.db")) as (server,client,state):
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Health"}) as response:
                cid=(await response.json())["conversation"]["id"]
            async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"),headers=headers(cookie),
                    json={"content":"У меня астма. Как безопасно похудеть?","model":"velia-quantum","idempotency_key":"quantum-health-01"}) as response:
                wire=await response.text()
                assert response.status==200 and "[DONE]" in wire
                assert "www.nhs.uk" in wire and "attacker.example" not in wire
            assert state["search_queries"]==["healthy weight loss advice"]
            assert state["search_payloads"][0]["include_domains_mode"]=="restrict"
            assert state["intent_payloads"]==state["forbidden_model_calls"]==[]
            assert "астма" in state["account_calls"][-1]["content"]
    asyncio.run(run())
