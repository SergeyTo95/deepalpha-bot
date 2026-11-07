"""Bounded, user-triggered place lookup. No public Nominatim dependency."""
import asyncio
import hashlib
import os
import re
import time
from collections import OrderedDict
from urllib.parse import urlsplit
from aiohttp import web, ClientSession, ClientTimeout, ClientError
from velia_desktop_routes import AuthenticationUnavailable

CATEGORIES={
    'airport':('aeroway:aerodrome',), 'hospital':('amenity:hospital',),
    'clinic':('amenity:clinic','healthcare:clinic'), 'pharmacy':('amenity:pharmacy',),
    'hotel':('tourism:hotel',), 'restaurant':('amenity:restaurant',),
    'cafe':('amenity:cafe',), 'museum':('tourism:museum',),
    'station':('railway:station','public_transport:station','amenity:bus_station'),
    'school':('amenity:school',), 'university':('amenity:university',),
}

def search_filters(body):
    category=body.get('category','');country=body.get('country_code','')
    if not isinstance(category,str) or category and category not in CATEGORIES:raise ValueError()
    if not isinstance(country,str) or country and not re.fullmatch('[A-Za-z]{2}',country):raise ValueError()
    return {'category':category,'country_code':country.upper()}

def photon_params(query,filters):
    params=[('q',query),('limit','6'),('lang','en')]
    if filters['country_code']:params.append(('countrycode',filters['country_code']))
    params.extend(('osm_tag',tag) for tag in CATEGORIES.get(filters['category'],()))
    return params

def extract_places(value,filters):
    places=[]
    for feature in value.get('features',[])[:6]:
        coordinates=feature.get('geometry',{}).get('coordinates',[]);p=feature.get('properties',{})
        if len(coordinates)!=2:continue
        lon,lat=coordinates
        if isinstance(lat,bool) or isinstance(lon,bool) or not isinstance(lat,(float,int)) or not isinstance(lon,(float,int)) or not -90<=lat<=90 or not -180<=lon<=180:continue
        country=str(p.get('countrycode','')).upper();tag=str(p.get('osm_key',''))+':'+str(p.get('osm_value',''))
        category=next((key for key,tags in CATEGORIES.items() if tag in tags),'')
        if filters['country_code'] and country!=filters['country_code']:continue
        if filters['category'] and category!=filters['category']:continue
        title=str(p.get('name') or p.get('street') or p.get('city') or 'Место')[:200]
        label=', '.join(str(p[key]) for key in ['street','housenumber','city','state','country'] if p.get(key))[:500]
        places.append({'title':title,'address':label,'lat':lat,'lon':lon,'country_code':country if re.fullmatch('[A-Z]{2}',country) else '', 'category':category})
    return places


def setup_map_routes(app,*,session_for,same_origin,json_response,origin,lookup=None):
    cache=OrderedDict();users={};lock=asyncio.Lock();last=0;day=0;count=0
    endpoint=os.getenv('VELIA_MAP_GEOCODER_URL','https://photon.komoot.io/api/')
    parsed=urlsplit(endpoint)
    available=parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    async def remote(query,filters):
        if not available:raise ValueError()
        async with ClientSession(timeout=ClientTimeout(total=8),trust_env=False) as client:
            async with client.get(endpoint,params=photon_params(query,filters),headers={'User-Agent':'VELIA/1.0 (+'+origin+')','Accept':'application/json'},allow_redirects=False) as response:
                if response.status!=200:raise ValueError()
                data=await response.content.read(250001)
                if len(data)>250000:raise ValueError()
                import json
                return json.loads(data)
    async def search(request):
        nonlocal last,day,count
        if not same_origin(request):return json_response({'ok':False,'error':'invalid_origin'},403)
        try:session=await session_for(request)
        except AuthenticationUnavailable:return json_response({'ok':False,'error':'authentication_unavailable'},503)
        if not session:return json_response({'ok':False,'error':'unauthorized'},401)
        try:
            body=await request.json()
            if not isinstance(body,dict) or not isinstance(body.get('query'),str):return json_response({'ok':False,'error':'invalid_map_query'},400)
            query=body['query'].strip()
            if not 2<=len(query)<=200:return json_response({'ok':False,'error':'invalid_map_query'},400)
            try:filters=search_filters(body)
            except ValueError:return json_response({'ok':False,'error':'invalid_map_query'},400)
            owner=str(session.user_id);now=time.monotonic();recent=[t for t in users.get(owner,[]) if t>now-3600]
            if len(recent)>=30:return json_response({'ok':False,'error':'map_rate_limited'},429)
            if len(users)>512:users.clear()
            users[owner]=recent+[now]
            key=hashlib.sha256((query.casefold()+'\0'+filters['category']+'\0'+filters['country_code']).encode()).hexdigest()
            if key in cache and cache[key][0]>now:
                cache.move_to_end(key);return json_response({'ok':True,'places':cache[key][1]})
            today=int(time.time()//86400)
            if day!=today:day=today;count=0
            if lock.locked() or count>=1000:return json_response({'ok':False,'error':'map_rate_limited'},429)
            async with lock:
                if last>now-1:await asyncio.sleep(1-(now-last))
                last=time.monotonic();count+=1
                value=await (lookup or remote)(query,filters)
            places=extract_places(value,filters)
            cache[key]=(time.monotonic()+86400,places)
            while len(cache)>500:cache.popitem(last=False)
            response=json_response({'ok':True,'places':places});response.headers['Cache-Control']='no-store';return response
        except (ValueError,TypeError,AttributeError,ClientError,TimeoutError):return json_response({'ok':False,'error':'map_search_unavailable'},503)
    app.router.add_post('/web-api/v1/map/search',search)
