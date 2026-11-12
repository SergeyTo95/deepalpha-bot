"""A fixed action catalog; model plans are data, never executable code."""
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MODELS={'velia-flash','velia-quantum','velia-pro'}
TOOLS={
 'finance.report':('Финансовая сводка','read',{'query':'optional object: preset, period_start, period_end, project, base_currency','reserve_months':'optional integer 1..24'}),
 'crm.search':('Контакты и договорённости','read',{'query':'text to search contacts'}),
 'notebook.search':('Заметки и память','read',{'query':'text to search saved records'}),
 'web.search':('Поиск открытых источников','read',{'query':'public search text'}),
 'calendar.list':('События календаря','read',{'time_min':'RFC3339 with offset','time_max':'RFC3339 with offset'}),
 'text.draft':('Подготовить черновик','draft',{'instruction':'what to write using earlier step results'}),
 'document.export':('Создать файл','file',{'source_step':'earlier text.draft step id','format':'pdf|docx|pptx|xlsx','title':'file title'}),
 'notebook.save':('Сохранить в заметки','idempotent',{'source_step':'earlier text.draft step id','title':'note title'}),
 'calendar.create':('Создать событие','external',{'title':'event title','start':'RFC3339 with offset','end':'RFC3339 with offset','time_zone':'IANA time zone'}),
 'gmail.draft':('Создать черновик Gmail','external',{'to':'one exact email explicitly supplied in the goal','subject':'subject','source_step':'earlier text.draft step id'}),
 'review':('Согласовать результат','review',{'source_step':'earlier text.draft step id'}),
}

class MissionError(Exception):
    def __init__(self,code='mission_invalid_plan',status=400):
        self.code,self.status=code,status
        super().__init__(code)

def require(ok,code='mission_invalid_plan',status=400):
    if not ok:raise MissionError(code,status)

def text(value,limit):return isinstance(value,str) and 0<len(value.strip())<=limit

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def validate_plan(plan,goal):
    require(text(goal,6000))
    require(isinstance(plan,dict) and set(plan)=={'title','steps'} and text(plan['title'],160))
    steps=plan['steps'];require(isinstance(steps,list) and 1<=len(steps)<=8)
    known={};counts={}
    for step in steps:
        require(isinstance(step,dict) and set(step)=={'id','title','tool','arguments'})
        id,name,a=step['id'],step['tool'],step['arguments']
        require(isinstance(id,str) and re.fullmatch(r's[1-8]',id) and id not in known and text(step['title'],160))
        require(isinstance(name,str) and name in TOOLS and isinstance(a,dict))
        counts[name]=counts.get(name,0)+1
        if name=='finance.report':
            require(set(a)<={'query','reserve_months'})
            q=a.get('query',{});require(isinstance(q,dict) and set(q)<={'preset','period_start','period_end','project','base_currency','category'})
            require(all(text(v,160) for v in q.values()))
            require(type(a.get('reserve_months',6)) is int and 1<=a.get('reserve_months',6)<=24)
        elif name in {'crm.search','notebook.search','web.search'}:
            require(set(a)=={'query'} and text(a['query'],240))
        elif name=='calendar.list':
            require(set(a)=={'time_min','time_max'} and all(text(v,60) for v in a.values()))
            from desktop.workspace_connections import calendar_window
            try:calendar_window(a,default=False)
            except Exception:raise MissionError('mission_calendar_invalid') from None
        elif name=='text.draft':require(set(a)=={'instruction'} and text(a['instruction'],2000))
        elif name=='calendar.create':
            require(set(a)=={'title','start','end','time_zone'} and text(a['title'],300))
            from desktop.workspace_connections import calendar_create_payload
            try:calendar_create_payload(a)
            except Exception:raise MissionError('mission_calendar_invalid') from None
        else:
            fields={'document.export':{'source_step','format','title'},'notebook.save':{'source_step','title'},'gmail.draft':{'to','subject','source_step'},'review':{'source_step'}}[name]
            require(set(a)==fields and isinstance(a.get('source_step'),str) and known.get(a['source_step'])=='text.draft')
            if name=='document.export':require(a['format'] in {'pdf','docx','pptx','xlsx'} and text(a['title'],160))
            if name=='notebook.save':require(text(a['title'],160))
            if name=='gmail.draft':
                require(text(a['subject'],300) and '\r' not in a['subject'] and '\n' not in a['subject'])
                recipient=a['to'];require(isinstance(recipient,str) and len(recipient)<=320 and re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',recipient),'mission_recipient_required')
                addresses=re.findall(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;.!?]+',goal)
                require(any(recipient.casefold()==v.strip('"\'()').casefold() for v in addresses),'mission_recipient_required')
        known[id]=name
    require(counts.get('text.draft',0)<=2 and counts.get('document.export',0)<=3 and counts.get('web.search',0)<=1,'mission_step_limit')
    require(len(json.dumps(plan,ensure_ascii=False))<=10000)
    return plan

def timezone(value):
    require(text(value,80),'mission_invalid_timezone')
    try:ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError):raise MissionError('mission_invalid_timezone') from None
    return value
