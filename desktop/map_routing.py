"""Free-plan ORS routing only. No paid fallback, geocoding, names or account data."""
import asyncio
from collections import OrderedDict, deque
import hashlib
import itertools
import json
import math
import os
import time
from aiohttp import ClientSession, ClientTimeout, ClientError, web
from velia_desktop_routes import AuthenticationUnavailable

API='https://api.heigit.org/openrouteservice/v2'
PROFILES={'driving':'driving-car','walking':'foot-walking','bicycling':'cycling-regular'}


class RouteError(ValueError):
    def __init__(self,code='route_unavailable',status=503):
        self.code,self.status=code,status
        super().__init__(code)


def require(value,code='route_invalid_data',status=400):
    if not value:raise RouteError(code,status)


def number(value,maximum):
    return type(value) in (int,float) and math.isfinite(value) and 0<=value<=maximum


def route_input(data):
    require(isinstance(data,dict) and set(data)<={'coordinates','mode','optimize'} and 'coordinates' in data)
    coordinates=data['coordinates'];mode=data.get('mode','driving');optimize=data.get('optimize',False)
    require(isinstance(coordinates,list) and 2<=len(coordinates)<=5)
    require(type(optimize) is bool and isinstance(mode,str))
    require(mode in PROFILES,'route_mode_unavailable',422)
    require(all(isinstance(p,list) and len(p)==2 and number(p[0]+180 if type(p[0]) in (int,float) else None,360)
                and number(p[1]+90 if type(p[1]) in (int,float) else None,180) for p in coordinates))
    require(len({tuple(p) for p in coordinates})==len(coordinates),'route_duplicate_points')
    return coordinates,mode,optimize


def best_order(durations,size):
    require(isinstance(durations,list) and len(durations)==size,'route_invalid_response',502)
    require(all(isinstance(row,list) and len(row)==size and all(v is None or number(v,604800) for v in row) for row in durations),'route_invalid_response',502)
    # Start and final destination are fixed. Intermediate stops only, directed costs.
    choices=[]
    for middle in itertools.permutations(range(1,size-1)):
        order=[0,*middle,size-1];legs=[durations[a][b] for a,b in zip(order,order[1:])]
        if all(v is not None for v in legs):choices.append((sum(legs),order))
    require(choices,'route_no_path',422)
    return min(choices,key=lambda choice:(choice[0],choice[1]))[1]


def extract_route(raw,coordinates,order,mode,optimized):
    require(isinstance(raw,dict),'route_invalid_response',502)
    features=raw.get('features');require(isinstance(features,list) and len(features)==1,'route_invalid_response',502)
    feature=features[0];require(isinstance(feature,dict),'route_invalid_response',502)
    geometry=feature.get('geometry',{});props=feature.get('properties',{})
    require(isinstance(geometry,dict) and isinstance(props,dict),'route_invalid_response',502)
    summary=props.get('summary',{});require(isinstance(summary,dict),'route_invalid_response',502)
    line=geometry.get('coordinates')
    require(geometry.get('type')=='LineString' and isinstance(line,list) and 2<=len(line)<=10000,'route_invalid_response',502)
    require(all(isinstance(p,list) and len(p)==2 and type(p[0]) in (int,float) and type(p[1]) in (int,float)
                and math.isfinite(p[0]) and math.isfinite(p[1]) and -180<=p[0]<=180 and -90<=p[1]<=90 for p in line),'route_invalid_response',502)
    require(number(summary.get('distance'),50000000) and number(summary.get('duration'),604800),'route_invalid_response',502)
    segments=props.get('segments');require(isinstance(segments,list) and len(segments)==len(order)-1,'route_invalid_response',502)
    legs=[]
    for segment in segments:
        require(isinstance(segment,dict) and number(segment.get('distance'),50000000) and number(segment.get('duration'),604800),'route_invalid_response',502)
        legs.append({'distance_m':round(segment['distance'],1),'duration_s':round(segment['duration'],1)})
    return {'provider':'openrouteservice','mode':mode,'input_coordinates':coordinates,'order':order,
            'distance_m':round(summary['distance'],1),'duration_s':round(summary['duration'],1),
            'geometry':line,'legs':legs,'optimized':optimized,'traffic':False,'calculated_at':int(time.time()),
            'attribution':'© openrouteservice by HeiGIT | Data © OpenStreetMap contributors',
            'license':'CC-BY-SA-4.0'}


class FreeRouter:
    def __init__(self,*,api_key=None,request=None,clock=time.monotonic):
        self.key=api_key if api_key is not None else os.getenv('VELIA_MAP_ORS_API_KEY','')
        self.request=request or self.remote;self.clock=clock;self.lock=asyncio.Lock();self.cache=OrderedDict()
        self.users={};self.day=0;self.counts={'directions':0,'matrix':0};self.last=None;self.cooldown=0

    async def remote(self,path,body):
        async with ClientSession(timeout=ClientTimeout(total=12,connect=4),trust_env=False) as client:
            async with client.post(API+path,json=body,headers={'Authorization':self.key,'Accept':'application/geo+json,application/json'},allow_redirects=False) as response:
                if response.status==429:raise RouteError('route_free_limit',429)
                if response.status in (401,403):raise RouteError('route_access_denied',503)
                if response.status in (400,404):raise RouteError('route_no_path',422)
                if response.status!=200:raise RouteError()
                raw=await response.content.read(1000001)
                require(len(raw)<=1000000,'route_response_too_large',502)
                try:return json.loads(raw)
                except (ValueError,UnicodeError):raise RouteError('route_invalid_response',502) from None

    async def call(self,kind,path,body):
        now=self.clock()
        if self.cooldown>now:raise RouteError('route_free_limit',429)
        today=int(time.time()//86400)
        if self.day!=today:self.day=today;self.counts={'directions':0,'matrix':0}
        if self.counts[kind]>={'directions':800,'matrix':100}[kind]:raise RouteError('route_free_limit',429)
        if self.last is not None and now-self.last<2:await asyncio.sleep(2-(now-self.last))
        self.last=self.clock();self.counts[kind]+=1
        try:return await self.request(path,body)
        except RouteError as exc:
            if exc.code in ('route_free_limit','route_access_denied'):self.cooldown=self.clock()+60
            raise

    async def route(self,user,data):
        coordinates,mode,optimize=route_input(data)
        if not self.key:raise RouteError('route_key_required',503)
        now=self.clock();owner=str(user)
        for key in list(self.users):
            if self.users[key][-1]<=now-3600:del self.users[key]
        if owner not in self.users and len(self.users)>=1024:raise RouteError('route_busy',429)
        recent=self.users.setdefault(owner,deque())
        while recent and recent[0]<=now-3600:recent.popleft()
        if len(recent)>=12:raise RouteError('route_user_limit',429)
        recent.append(now)
        fingerprint=hashlib.sha256(json.dumps([owner,coordinates,mode,optimize],separators=(',',':')).encode()).hexdigest()
        cached=self.cache.get(fingerprint)
        if cached and cached[0]>now:
            self.cache.move_to_end(fingerprint);return {**cached[1],'cached':True}
        if self.lock.locked():raise RouteError('route_busy',429)
        async with self.lock,asyncio.timeout(32):
            order=list(range(len(coordinates)));profile=PROFILES[mode]
            if optimize and len(coordinates)>3:
                matrix=await self.call('matrix','/matrix/'+profile,{'locations':coordinates,'metrics':['duration']})
                require(isinstance(matrix,dict),'route_invalid_response',502)
                order=best_order(matrix.get('durations'),len(coordinates))
            raw=await self.call('directions','/directions/'+profile+'/geojson',{'coordinates':[coordinates[i] for i in order],'instructions':False,'geometry_simplify':True})
            result=extract_route(raw,coordinates,order,mode,optimize)
            self.cache[fingerprint]=(self.clock()+600,result)
            def expire():
                entry=self.cache.get(fingerprint)
                if entry and entry[0]<=self.clock():self.cache.pop(fingerprint,None)
            asyncio.get_running_loop().call_later(600,expire)
            while len(self.cache)>64:self.cache.popitem(last=False)
            return {**result,'cached':False}


def setup_routing_routes(app,*,session_for,same_origin,json_response,router=None):
    router=router or FreeRouter()
    async def handle(request):
        if not same_origin(request):return json_response({'ok':False,'error':'invalid_origin'},403)
        try:
            session=await session_for(request)
            if not session:return json_response({'ok':False,'error':'unauthorized'},401)
            data=await request.clone(client_max_size=4096).json()
            result=await router.route(session.user_id,data)
            return json_response({'ok':True,'route':result})
        except AuthenticationUnavailable:return json_response({'ok':False,'error':'authentication_unavailable'},503)
        except RouteError as exc:return json_response({'ok':False,'error':exc.code},exc.status)
        except web.HTTPRequestEntityTooLarge:return json_response({'ok':False,'error':'route_invalid_data'},413)
        except (ValueError,TypeError,UnicodeError):return json_response({'ok':False,'error':'route_invalid_data'},400)
        except (ClientError,TimeoutError):return json_response({'ok':False,'error':'route_unavailable'},503)
    app.router.add_post('/web-api/v1/map/route',handle)
