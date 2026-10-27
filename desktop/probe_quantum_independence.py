"""One synthetic, free-only Quantum planning call with Flash forbidden."""
import asyncio
import json
import os
import re
import sys
import time

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar
from desktop import decision_router, request_intent, web_search, quantum_intent
from services.velia_structured_output import strict_json_loads
from velia_quantum_provider import EXPERIENTIAL_URL, FREE_MODEL, EXPERIENTIAL_MODELS, configuration, QuantumError, verify_zero_spend_async

MODELS_MAX_BYTES = 1024 * 1024


async def check_access(config):
    """One authenticated read, no model generation, billing, or secret output."""
    if (config is None or config.provider != "experiential"
            or config.base_url != EXPERIENTIAL_URL or config.model not in EXPERIENTIAL_MODELS):
        print('VELIA_QUANTUM_ACCESS '+json.dumps({'ok':False,'status':0,'provider_code':'invalid_local_config'}),flush=True)
        raise RuntimeError('quantum_free_configuration_missing')
    source='VELIA_QUANTUM_API_KEY' if os.getenv('VELIA_QUANTUM_API_KEY','').strip() else 'EXPLABS_API_KEY'
    shape=('unresolved_reference' if '${{' in config.api_key else
        'expected' if re.fullmatch(r'xpl_[0-9a-f]{40}',config.api_key) else 'unexpected')
    record={'ok':False,'status':0,'provider_code':'not_received','key_source':source,'key_shape':shape}
    try:
        if shape=='unresolved_reference':
            record['provider_code']='unresolved_key_reference'
            raise RuntimeError('quantum_access_failed')
        async with ClientSession(timeout=ClientTimeout(total=10,connect=3),
                cookie_jar=DummyCookieJar(),trust_env=False) as client:
            async with client.get(config.base_url+'/models',
                    headers={'Authorization':'Bearer '+config.api_key},allow_redirects=False) as response:
                record['status']=response.status
                if response.status!=200:
                    record['provider_code']=await quantum_intent.provider_error_code(response)
                    raise RuntimeError('quantum_access_failed')
                record['provider_code']='invalid_models_response'
                raw=bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw)>MODELS_MAX_BYTES:
                        record['provider_code']='models_response_too_large'
                        raise RuntimeError('quantum_access_failed')
                try:
                    # A catalog is larger than a model's tool arguments. Match
                    # the HTTP read budget without changing decision validation.
                    body=strict_json_loads(raw.decode('utf-8'),max_bytes=MODELS_MAX_BYTES)
                except ValueError:
                    record['provider_code']='invalid_models_json'
                    raise RuntimeError('quantum_access_failed') from None
                if not isinstance(body,dict) or not isinstance(body.get('data'),list):
                    record['provider_code']='invalid_models_envelope'
                    raise RuntimeError('quantum_access_failed')
                ids={row['id'] for row in body['data'] if isinstance(row,dict) and isinstance(row.get('id'),str)}
                canonical = config.model.removesuffix(':free')
                record.update(base_model_listed=canonical in ids or config.model in ids,
                    free_alias_listed=canonical+':free' in ids)
                if not record['base_model_listed']:
                    record['provider_code']='model_not_granted'
                    raise RuntimeError('quantum_access_failed')
                record['provider_code']='zero_spend_guard_required'
                await verify_zero_spend_async(client,config)
                record.update(ok=True,provider_code='none',zero_spend_key=True)
    except QuantumError:
        raise RuntimeError('quantum_access_failed') from None
    finally:
        print('VELIA_QUANTUM_ACCESS '+json.dumps(record),flush=True)


def response_shape(result, question=''):
    """Only structural metadata from a fixed synthetic probe, never model text."""
    try:
        choice=result['choices'][0];call=choice['message']['tool_calls'][0]['function']
        raw=call.get('arguments');args=json.loads(raw) if isinstance(raw,str) else raw
        known={'action','quote','candidate','query','source_scope','task_query','context'}
        return {'finish':choice.get('finish_reason') if choice.get('finish_reason') in {'stop','tool_calls','length','content_filter'} else 'other',
            'format':'object' if isinstance(raw,dict) else 'json','known_fields':sorted(set(args)&known),
            'null_fields':sorted(k for k in known if k in args and args[k] is None),
            'unknown_fields':len(set(args)-known),
            'action':args.get('action') if args.get('action') in {'direct','search','clarify'} else 'other',
            'context_items':len(args['context']) if isinstance(args.get('context'),list) else -1,
            'quote_exact':isinstance(args.get('quote'),str) and (not args['quote'] or args['quote'] in question),
            'candidate_empty':args.get('candidate')=='',
            'scope':args.get('source_scope') if args.get('source_scope') in {'general','official_health'} else 'other',
            'query_words':len(args['query'].split()) if isinstance(args.get('query'),str) else -1,
            'task_words':len(args['task_query'].split()) if isinstance(args.get('task_query'),str) else -1,
            'context_exact':[isinstance(v,dict) and isinstance(v.get('quote'),str) and bool(v['quote']) and v['quote'] in question for v in args.get('context',[])][:6] if isinstance(args.get('context'),list) else []}
    except Exception:
        return {'envelope':'invalid'}


async def run(*, voice=False, access_only=False, config_only=False):
    if config_only:
        if voice or access_only:
            raise RuntimeError("incompatible_probe_modes")
        if os.getenv("VELIA_QUANTUM_ENABLED", "").strip().lower() not in {"true", "1", "yes", "on"}:
            print("VELIA_QUANTUM_CONFIG_DISABLED", flush=True)
            return
        config = configuration()
        if config is None:
            raise RuntimeError("quantum_invalid_configuration")
        print("VELIA_QUANTUM_CONFIG_OK provider=" + config.provider, flush=True)
        return
    def forbidden(*args, **kwargs):
        raise RuntimeError("quantum_touched_another_model")
    # Process-local changes cannot affect the following gateway process.
    for name in ("VELIA_DESKTOP_FLASH_ENABLED", "VELIA_DESKTOP_FLASH_API_KEY",
            "VELIA_DESKTOP_FLASH_BASE_URL", "VELIA_DECISION_API_KEY"):
        os.environ.pop(name, None)
    request_intent.understand = forbidden
    request_intent.flash_endpoint = forbidden
    request_intent.check_flash_context = forbidden
    web_search.understand = forbidden
    decision_router.try_direct = forbidden
    config = configuration()
    await check_access(config)
    if access_only:return
    started = time.monotonic()
    cases=[('direct','Составь короткий план вымышленного рассказа о дружбе робота и птицы.')]
    if voice:
        original=quantum_intent.planning_decision
        def observe(result, question):
            try:return original(result,question)
            except Exception:
                print('VELIA_QUANTUM_PLAN_SHAPE '+json.dumps(response_shape(result,question)),flush=True)
                raise
        quantum_intent.planning_decision=observe
        cases=[('direct','Объясни разницу между списком и множеством.'),
            ('search','Найди последнюю стабильную версию Python.'),
            ('medical','Как безопасно снизить вес при астме?')]
    for label,question in cases:
        turn=time.monotonic()
        result=await web_search.WebSearch()._understand([{'role':'user','content':question}],model='velia-quantum')
        if (result.get('action')!=('direct' if label=='direct' else 'search')
                or label=='medical' and result.get('source_scope')!='official_health'):
            raise RuntimeError('quantum_plan_acceptance_failed')
        if voice:print('VELIA_QUANTUM_VOICE_PLAN_OK '+json.dumps({'case':label,'elapsed_ms':round((time.monotonic()-turn)*1000)}),flush=True)
    if voice:
        from desktop.voice_routes import synthesize,validate,voice_catalog
        if not any(v['voiceURI']=='edge:en-US-AvaMultilingualNeural' for v in voice_catalog()):
            raise RuntimeError('voice_provider_not_configured')
        turn=time.monotonic()
        audio=await synthesize(validate({'text':'Привет! Я Велия.','voice':'edge:en-US-AvaMultilingualNeural','rate':1}))
        if len(audio)<1000:raise RuntimeError('voice_audio_unavailable')
        print('VELIA_VOICE_PIPELINE_OK '+json.dumps({'bytes':len(audio),'synthesis_ms':round((time.monotonic()-turn)*1000),'flash_calls':0}),flush=True)
    print("VELIA_QUANTUM_INDEPENDENCE_OK " + json.dumps({"model":FREE_MODEL,
        "flash_calls":0,"elapsed_ms":round((time.monotonic()-started)*1000)}), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(run(voice='--voice' in sys.argv,access_only='--access' in sys.argv,config_only='--config-only' in sys.argv))
    except Exception:
        print("VELIA_QUANTUM_INDEPENDENCE_FAILED", flush=True)
        raise SystemExit(1) from None
