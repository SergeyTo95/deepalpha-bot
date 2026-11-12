"""Text-only mission calls through the gateway's pinned selected-model routes.

The caller owns account authorization and the background admission slot. No
conversation sender, agent router, tools or provider fallback runs in this path.
"""
from aiohttp import ClientSession, ClientTimeout, DummyCookieJar
import os
import velia_free_models as free_models
import velia_quantum_provider as quantum
from velia_quantum_transport import completion_async
from velia_desktop_routes import (FLASH_ID, QUANTUM_ID, flash_enabled,
    flash_endpoint, validate_payload, check_flash_context)
from desktop.mission_schema import MissionError, require


async def read_text(response):
    require('text/event-stream' in response.headers.get('Content-Type',''), 'mission_model_unavailable',502)
    decoder=quantum.StreamDecoder();parts=[];size=0
    async for chunk in response.content.iter_chunked(8192):
        for event in decoder.feed(chunk):
            delta=event['choices'][0]['delta']
            require(not delta.get('tool_calls'),'mission_model_unavailable',502)
            text=delta.get('content','');size+=len(text)
            require(size<=8000,'mission_result_too_large',502);parts.append(text)
    decoder.finish()
    require(decoder.finish_reason=='stop','mission_model_incomplete',502)
    result=''.join(parts).strip()
    require(bool(result),'mission_model_unavailable',502)
    return result


async def generate_text(model,messages,*,max_tokens=4096):
    if model==FLASH_ID:require(flash_enabled(),'flash_unavailable',503)
    # Use exactly the same payload builder, model IDs, credentials and limits as
    # ordinary gateway chat. In particular, free-tier misconfiguration fails closed.
    payload=validate_payload({'model':model,'messages':messages,'stream':True,'max_tokens':max_tokens})
    config=(free_models.configuration(model.removeprefix('velia-')) if free_models.enabled()
        else quantum.configuration() if model==QUANTUM_ID else None)
    if model==QUANTUM_ID or free_models.enabled():require(config is not None,'mission_model_unavailable',503)
    async with ClientSession(timeout=ClientTimeout(total=95,sock_read=90),cookie_jar=DummyCookieJar(),trust_env=False) as client:
        if config:
            # Existing spend guard and overload handling; no alternate model/key.
            async with completion_async(client,config,payload,retry_overload=True) as response:
                return await read_text(response)
        is_flash=model==FLASH_ID
        key=os.getenv('VELIA_DESKTOP_FLASH_API_KEY' if is_flash else 'KIMI_API_KEY','').strip()
        require(bool(key),'mission_model_unavailable',503)
        endpoint=flash_endpoint() if is_flash else os.getenv('KIMI_BASE_URL','https://api.moonshot.ai/v1').rstrip('/')
        headers={'Authorization':'Bearer '+key}
        if is_flash:await check_flash_context(client,endpoint,headers,payload)
        async with client.post(endpoint+('/v1/chat/completions' if is_flash else '/chat/completions'),
                json=payload,headers=headers,allow_redirects=False) as response:
            require(response.status==200,'mission_model_unavailable',502)
            return await read_text(response)
