"""Conservative direct tasks: no facts, medical advice or inferred user intent.

The answer still uses Flash and the unchanged original messages. Anything
outside this small, explicit set retains the full interpretation pipeline.
"""
import asyncio
import re
from desktop.spelling_hints import spelling_hints

_GREETINGS = frozenset({'привет','здравствуйте','здравствуй','доброе утро','добрый день',
    'добрый вечер','hello','hi','good morning','merhaba','selam'})
_ARITHMETIC = re.compile(r'\d{1,12}(?:\s*[+*×]\s*\d{1,12}){1,4}\s*=?')
_TRANSLATION = re.compile(r'^(?:переведи(?: этот текст)? на (?:английский|русский|турецкий|немецкий|французский|испанский|китайский)(?: язык)?|translate(?: this text)? (?:into|to) (?:english|russian|turkish|german|french|spanish|chinese))\s*:\s*\S',re.I)
_EDIT = re.compile(r'^(?:сократи (?:этот )?текст|исправь пунктуацию|shorten (?:this )?text|correct punctuation)\s*:\s*\S',re.I)

async def direct_task(messages):
    history=[m for m in messages if m.get('role') in {'user','assistant'}]
    if len(history)!=1 or history[0].get('role')!='user':
        return None
    content=history[0].get('content')
    if not isinstance(content,str):
        return None
    text=content.strip()
    greeting=text.casefold().rstrip('!.')
    if greeting in _GREETINGS or _ARITHMETIC.fullmatch(text):
        return {'action':'direct','query':'','context':[]}
    if _TRANSLATION.match(text) or _EDIT.match(text):
        # Unknown spellings/terms keep the existing model-based restoration.
        if not await asyncio.to_thread(spelling_hints,text):
            return {'action':'direct','query':'','context':[]}
    return None
