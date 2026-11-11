"""Explicit, bounded Gmail history import. Reads never mark mail read or send it."""
import asyncio
from datetime import datetime, time as midnight, timedelta, timezone
from email.utils import getaddresses
import html
import json
import re
import secrets
import time
from zoneinfo import ZoneInfo
from cryptography.fernet import InvalidToken
from desktop.crm import CrmError, day, fields, local_today, require, string


def clean(value, limit):
    return ''.join(c for c in html.unescape(str(value)) if ord(c)>=32 or c in '\n\t')[:limit].strip()


def bounds(start, end, zone):
    local_today(zone)
    start,end=day(start),day(end)
    require(start<=end and end<day('9999-12-31'),'crm_invalid_date')
    tz=ZoneInfo(zone)
    return (int(datetime.combine(start,midnight(),tz).timestamp()),
            int(datetime.combine(end+timedelta(days=1),midnight(),tz).timestamp()))


def mail_entry(message, contact, mailbox, start, end, zone, now=None):
    """Recheck exact addresses, labels and provider timestamp; never infer reply status."""
    require(isinstance(message,dict),'crm_invalid_mail_source')
    id=message.get('id','')
    require(isinstance(id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',id),'crm_invalid_mail_source')
    try:stamp=int(message['internalDate'])/1000
    except (KeyError,TypeError,ValueError):raise CrmError('crm_invalid_mail_source') from None
    if not start<=stamp<end or stamp>(now if now is not None else time.time()):return None
    labels=message.get('labelIds',[])
    if any(label in labels for label in ('DRAFT','TRASH','SPAM')):return None
    headers={}
    for row in message.get('payload',{}).get('headers',[]):
        headers.setdefault(str(row.get('name','')).lower(),[]).append(str(row.get('value','')))
    addresses=lambda name:{address.casefold() for _,address in getaddresses(headers.get(name,[]))}
    email=contact['email'].casefold();own=mailbox.casefold()
    if 'SENT' in labels:
        if own not in addresses('from') or email not in addresses('to')|addresses('cc'):return None
        direction='outbound'
    else:
        if email not in addresses('from'):return None
        direction='inbound'
    summary=clean(' · '.join([' '.join(headers.get('subject',[])),message.get('snippet','')]),1500).encode()[:2000].decode(errors='ignore') or 'Письмо без темы и фрагмента'
    return {'contact_id':contact['id'],'date':datetime.fromtimestamp(stamp,timezone.utc).astimezone(ZoneInfo(zone)).date().isoformat(),
            'time_zone':zone,'direction':direction,'channel':'email','summary':summary,'requires_reply':False,
            'source':{'provider':'gmail','mailbox':mailbox,'message_id':id}}


async def prepare_mail(bridge,owner,doc,contact_id,start_date,end_date,zone):
    contact=next((c for c in doc['contacts'] if c['id']==contact_id),None)
    require(contact is not None,'crm_contact_not_found',404)
    email=contact['email']
    # A literal query address, never user-controlled Gmail search operators.
    require(bool(re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}',email)),'crm_mail_email_required')
    start,end=bounds(start_date,end_date,zone)
    await bridge.restore(owner)
    connection=bridge.get(owner)
    require(not connection['lock'].locked(),'crm_mail_busy',409)
    async with connection['lock']:
        api=bridge.api_request
        profile=await api(connection,'GET','/gmail/v1/users/me/profile')
        mailbox=string(profile.get('emailAddress'),320)
        require(mailbox.casefold()!=email.casefold(),'crm_mail_self_contact')
        existing={(i['source']['mailbox'].casefold(),i['source']['message_id']) for i in doc['interactions'] if i.get('source',{}).get('provider')=='gmail'}
        found=await api(connection,'GET','/gmail/v1/users/me/messages',params={'q':f'{{from:{email} to:{email} cc:{email}}} after:{start-1} before:{end} -in:drafts -in:trash -in:spam','maxResults':30})
        rows=[];seen=set();duplicates=0;skipped=0;remaining=False
        for item in found.get('messages',[])[:30]:
            id=item.get('id','')
            require(isinstance(id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',id),'crm_invalid_mail_source')
            if id in seen:continue
            seen.add(id)
            if (mailbox.casefold(),id) in existing:duplicates+=1;continue
            if len(rows)>=8:remaining=True;break
            raw=await api(connection,'GET','/gmail/v1/users/me/messages/'+id,params={'format':'metadata','metadataHeaders':['From','To','Cc','Subject']})
            require(raw.get('id')==id,'crm_invalid_mail_source')
            row=mail_entry(raw,contact,mailbox,start,end,zone)
            if row:rows.append(row)
            else:skipped+=1
        # If the user disconnected/replaced Google during an awaited read, do not return its data.
        bridge.get(owner,connection['id'])
        return {'rows':rows,'duplicates':duplicates,'skipped':skipped,'more':remaining or bool(found.get('nextPageToken')),
                'mailbox':mailbox,'contact':contact['name'],'connection_id':connection['id']}


async def import_mail(action,data,session,store,bridge):
    require(bridge is not None,'crm_mail_unavailable',503)
    owner=(str(session.user_id),session.id)
    if action=='mail-preview':
        fields(data,('contact_id','start_date','end_date','time_zone','revision'))
        current=await asyncio.to_thread(store.load,session.user_id)
        require(current['revision']==data['revision'],'crm_revision_conflict',409)
        result=await prepare_mail(bridge,owner,current['document'],string(data['contact_id'],100),data['start_date'],data['end_date'],data['time_zone'])
        rows=result.pop('rows')
        if not rows:return {**result,'count':0,'preview':[]}
        edit={'type':'batch','edits':[{'type':'add_interaction','interaction':row} for row in rows]}
        operation=secrets.token_hex(16)
        await asyncio.to_thread(store.preview,session.user_id,edit,data['revision'],operation,time_zone=data['time_zone'])
        payload={'kind':'crm-mail-v1','owner':list(owner),'expires':time.time()+600,'revision':data['revision'],
                 'operation':operation,'edit':edit,'time_zone':data['time_zone'],'connection_id':result['connection_id']}
        token=store.cipher.encrypt(json.dumps(payload,ensure_ascii=False).encode()).decode()
        return {**result,'count':len(rows),'token':token,'preview':[{'date':r['date'],'direction':r['direction'],'summary':r['summary']} for r in rows]}
    fields(data,('token','confirmed'))
    require(data['confirmed'] is True,'crm_confirmation_required',409)
    try:payload=json.loads(store.cipher.decrypt(string(data['token'],48000).encode(),ttl=600))
    except (InvalidToken,ValueError,TypeError):raise CrmError('crm_mail_preview_expired',409) from None
    require(payload.get('kind')=='crm-mail-v1' and payload.get('owner')==list(owner) and payload['expires']>=time.time(),'crm_mail_preview_expired',409)
    bridge.get(owner,payload['connection_id'])
    saved,doc=await asyncio.to_thread(store.commit,session.user_id,payload['edit'],payload['revision'],payload['operation'],time_zone=payload['time_zone'])
    return {**saved,'count':len(payload['edit']['edits'])}
