"""One synthetic, free-only Quantum planning call with Flash forbidden."""
import asyncio
import json
import os
import sys
import time

from desktop import decision_router, request_intent, web_search, quantum_intent
from velia_quantum_provider import FREE_MODEL, configuration


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


async def run(*, voice=False):
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
    if config is None or config.provider != "experiential" or config.model != FREE_MODEL:
        raise RuntimeError("quantum_free_configuration_missing")
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
        asyncio.run(run(voice='--voice' in sys.argv))
    except Exception:
        print("VELIA_QUANTUM_INDEPENDENCE_FAILED", flush=True)
        raise SystemExit(1) from None
