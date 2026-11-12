"""Conversation compatibility: task routing and durable results for older clients."""
from datetime import datetime,timezone
import re
import uuid

EXCLUDED=re.compile(r'^(?:напомни|запомни|покажи|открой|объясни кратко|что такое|как дела)|калькулятор|интерактивн|репозитор|деплой|github|каждый день|каждые|по расписанию',re.I)
COMPLEX=re.compile(r'(?:сделай|выполни|подготовь|проанализируй|исследуй|сравни|составь|разработай).{0,120}(?:в фоне|подробн.{0,25}(?:анализ|отч[её]т|план)|бизнес[- ]план|стратеги|глубок.{0,25}(?:анализ|исследован))|(?:подробн|глубок).{0,25}(?:проанализируй|исследуй)|исследуй.{0,25}подробн|^(?:research|analyze|analyse|prepare|compare).{0,100}(?:detailed|in depth|comprehensive|in the background)',re.I)
ARTIFACT=re.compile(r'^(?:велия[,\s]+)?(?:создай|сделай|подготовь|сформируй|экспортируй|create|make|prepare|export)(?=\s).{0,160}(?:\bPDF\b|\bDOCX\b|\bPPTX\b|\bXLSX\b|презентаци|документ)',re.I)
EXPLICIT=re.compile(r'^(?:велия[,\s]+)?(?:(?:выполни|создай|запусти|начни)\s+)?мисси[яю][:,\s]|^(?:run|create|start) (?:a )?mission\b',re.I)


def wants_task(content):
    text=content.strip()
    if EXPLICIT.search(text):return True
    if EXCLUDED.search(text):return False
    if COMPLEX.search(text) or ARTIFACT.search(text):return True
    if re.match(r'^(?:сравни|compare)',text,re.I) and len(text)>=120:return True
    if re.match(r'^(?:проанализируй|исследуй|разработай|подготовь|analyze|research)',text,re.I) and len(text)>=1000:return True
    return len(re.findall(r'собери|найди|проанализируй|подготовь|создай|сохрани|согласуй|назначь',text,re.I))>=3 and bool(re.search(r'отч[её]т|презентаци|документ|письмо|календар|заметк',text,re.I))


def accepted_text(m,origin):
    return 'Приняла задачу. Планирование и выполнение продолжатся в фоне. Результат сохранится в этом диалоге.\n\n[Открыть задачу]('+origin.rstrip('/')+'/?task='+m['id']+')'


def result_text(m,n,origin):
    state=n['task_status']
    if state=='waiting_input':return m.get('question') or 'Для продолжения задачи нужно уточнение. Открой карточку задачи.'
    if state=='awaiting_approval':return 'Для задачи «'+m['title']+'» нужно ваше подтверждение. Проверь действие в карточке задачи.'
    if state!='completed':return ('Нужно проверить результат действия. Автоматический повтор заблокирован.' if state=='uncertain' else 'Задача требует внимания. Сохранённые шаги доступны в карточке.')+'\n\n[Открыть задачу]('+origin.rstrip('/')+'/?task='+m['id']+')'
    parts=['Задача выполнена: «'+m['title']+'».']
    for s in m['steps']:
        r=s.get('result')
        if s['status']!='completed' or not r:continue
        if r['kind']=='draft':parts.append(r['data']['text'])
        elif r['kind']=='file':
            fmt=r['data']['format'];parts.append('[Скачать '+fmt.upper()+']('+origin.rstrip('/')+'/web-api/v1/missions/'+m['id']+'/files/'+s['id']+')')
        elif s['tool']=='gmail.draft':parts.append('Черновик создан в Gmail. Письмо не отправлено.')
        elif s['tool']=='calendar.create':parts.append('Событие создано: '+r['data']['event']['title']+'.')
        elif s['tool']=='notebook.save':parts.append('Сохранено в заметки: '+r['data']['title']+'.')
    return '\n\n'.join(parts)[:24000]


def conversation_messages(doc,cid,origin):
    out=[]
    for m in doc['missions']:
        source=m.get('source') or {}
        if not source.get('remote') or source.get('detached') or source.get('chat_id')!=cid:continue
        def record(key,role,content,at,**extra):
            return {'id':str(uuid.uuid5(uuid.UUID(m['id']),key)),'role':role,'content':content,'status':'completed','chat_mode':m['model'].removeprefix('velia-'),
                'created_at':datetime.fromtimestamp(at,timezone.utc).isoformat(),'mission_id':m['id'],**extra}
        out.append(record('request','user',m['goal'],m['created_at'],mission_origin=True))
        out.append(record('accepted','assistant',accepted_text(m,origin),m['created_at']+.001,mission_ack=True))
        latest=next((n for n in reversed(m.get('notices',[])) if n['task_status']==m['status']),None)
        for n in ([latest] if latest else []):
            if n['task_status']=='completed' and not all(s['status']=='completed' for s in m['steps']):continue
            out.append(record(n['id'],'assistant',result_text(m,n,origin),n['at'],mission_event_id=n['id']))
    return out
