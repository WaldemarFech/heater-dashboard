import json, os, sqlite3, threading, time
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool
from heater_logic import calculate_intervals, merge_energy_topics, normalize_events, validate_turn_on, history_window, normalize_status_samples

BASE = os.getenv('HEATER_MQTT_BASE','home/heater').rstrip('/')
HISTORY = [x.strip().rstrip('/') for x in os.getenv('HEATER_HISTORY_BASES', BASE).split(',') if x.strip()]
ALLOWED_HOSTS = {x.strip().lower() for x in os.getenv('HEATER_ALLOWED_HOSTS','localhost,127.0.0.1').split(',') if x.strip()}
PUBLIC_ORIGIN = os.getenv('HEATER_PUBLIC_ORIGIN','').rstrip('/')
STATIC = Path(__file__).resolve().parent/'static'
DATA = Path(os.getenv('HEATER_DATA_DIR','./data'))
LOCK = threading.Lock()
device={'online':False,'state':'unknown','updated':None,'power':None,'power_updated':None,'pending':False,'pending_since':None,'command_notice':None,'light_mode':None}
ON_CONFIRM_TIMEOUT=20.0
client=None

def record_offline():
    """Caller holds LOCK for MQTT events; unknown state always blocks ON."""
    if device['pending']:
        device['command_notice']='Verbindung unterbrochen; Einschaltbefehl nicht bestätigt.'
    device.update(online=False,state='unknown',updated=None,pending=False,pending_since=None)

def record_power(value,source,now=None,monotonic=None):
    """Apply fresh MQTT feedback. A timeout alone never releases pending."""
    now=time.time() if now is None else now
    monotonic=time.monotonic() if monotonic is None else monotonic
    device.update(state=value,updated=now)
    if value=='ON':
        if device['pending'] or (device['command_notice'] or '').startswith('Keine Einschaltbestätigung'):
            device['command_notice']='Einschalten von der Steckdose bestätigt.'
        device.update(pending=False,pending_since=None)
    elif (source=='telemetry' and device['online'] and device['pending'] and device['pending_since'] is not None
          and monotonic-device['pending_since']>=ON_CONFIRM_TIMEOUT):
        device.update(pending=False,pending_since=None,command_notice='Keine Einschaltbestätigung erhalten. Die Steckdose meldet weiterhin Aus; du kannst erneut einschalten.')

def settings_db():
    DATA.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA/'settings.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    return db

def read_tariff():
    with closing(settings_db()) as db:
        row=db.execute("SELECT value FROM settings WHERE key='tariff'").fetchone()
    return float(row[0]) if row else None

def connect_mqtt():
    global client
    import paho.mqtt.client as mqtt
    if not os.getenv('HEATER_MQTT_HOST') or not os.getenv('HEATER_MQTT_USER') or not os.getenv('HEATER_MQTT_PASSWORD'):
        return
    c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='heizstrahler-page-'+str(os.getpid()))
    c.username_pw_set(os.environ['HEATER_MQTT_USER'],os.environ['HEATER_MQTT_PASSWORD'])
    def on_connect(c,u,flags,reason,props):
        if reason == 0: c.subscribe([(BASE+'/tele/LWT',1),(BASE+'/tele/STATE',1),(BASE+'/tele/SENSOR',1),(BASE+'/stat/POWER',1),(BASE+'/config/ganztags',1)])
    def on_disconnect(c,u,flags,reason,props):
        with LOCK:
            record_offline()
            device['light_mode']=None
    def on_message(c,u,msg):
        try:
            text=msg.payload.decode('utf-8')
            with LOCK:
                if msg.topic == BASE+'/config/ganztags':
                    device['light_mode']=text if text in ('ON','OFF') else None
                elif msg.topic.endswith('/LWT'):
                    device['online']=text=='Online'
                    if text!='Online': record_offline()
                elif msg.topic.endswith('/STATE') and not msg.retain:
                    val=json.loads(text).get('POWER')
                    if val in ('ON','OFF'):
                        record_power(val,'telemetry')
                elif msg.topic.endswith('/SENSOR') and not msg.retain:
                    val=json.loads(text).get('ENERGY',{}).get('Power')
                    if isinstance(val,(float,int)) and not isinstance(val,bool): device.update(power=val,power_updated=time.time())
                elif msg.topic.endswith('/stat/POWER') and not msg.retain and text in ('ON','OFF'):
                    record_power(text,'stat')
        except (ValueError,UnicodeError,TypeError): pass
    c.on_connect=on_connect;c.on_disconnect=on_disconnect;c.on_message=on_message
    c.reconnect_delay_set(2,60)
    c.connect_async(os.environ['HEATER_MQTT_HOST'],int(os.getenv('HEATER_MQTT_PORT','1883')),30)
    c.loop_start();client=c

@asynccontextmanager
async def lifespan(app):
    settings_db().close()
    connect_mqtt()
    yield
    if client: client.loop_stop();client.disconnect()

app=FastAPI(title='Heizstrahler Ankleide',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.mount('/static',StaticFiles(directory=STATIC),name='static')

@app.middleware('http')
async def safety_headers(request,call_next):
    try:
        host=urlsplit('//'+request.headers.get('host','')).hostname
    except ValueError:
        host=None
    if not host or host.lower() not in ALLOWED_HOSTS:
        return JSONResponse({'detail':'Host nicht zugelassen'},status_code=400)
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin','')
        if not PUBLIC_ORIGIN or origin!=PUBLIC_ORIGIN or request.headers.get('x-heater-request')!='1' or urlsplit(origin).netloc.lower()!=request.headers.get('host','').lower():
            return JSONResponse({'detail':'Ungültiger Ursprung'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    return response

@app.get('/')
def index(): return FileResponse(STATIC/'index.html')

@app.get('/api/state')
def state():
    with LOCK:
        d=device.copy()
    age=round(time.time()-d['updated'],1) if d['updated'] else None
    fresh=bool(d['online'] and age is not None and 0<=age<=25)
    power_fresh=d['power_updated'] is not None and 0 <= time.time()-d['power_updated'] <= 25
    return {'online':d['online'],'state':d['state'] if fresh else 'unknown','age_seconds':age,'power_w':d['power'] if fresh and power_fresh else None,'can_turn_on':validate_turn_on(d['state'],d['online'],age,d['pending']),'command_notice':d['command_notice']}

@app.get('/api/database-status')
def database_status():
    dsn=os.getenv('HEATER_PG_DSN')
    if not dsn: raise HTTPException(503,'Messdatenbank nicht eingerichtet')
    try:
        import psycopg2
        with psycopg2.connect(dsn,connect_timeout=4,options='-c statement_timeout=3000') as db:
            with db.cursor() as cur:
                cur.execute('SELECT 1')
                if cur.fetchone() != (1,): raise RuntimeError('unexpected database response')
        return {'connected':True}
    except Exception:
        raise HTTPException(503,'Messdatenbank derzeit nicht erreichbar') from None

class Action(BaseModel): action:str

@app.post('/api/power')
def power(a:Action):
    if a.action not in ('ON','OFF'): raise HTTPException(422,'Unbekannter Befehl')
    if not client or not client.is_connected(): raise HTTPException(503,'MQTT-Verbindung nicht verfügbar')
    with LOCK:
        age=time.time()-device['updated'] if device['updated'] else None
        if a.action=='ON' and not validate_turn_on(device['state'],device['online'],age,device['pending']):
            raise HTTPException(409,'Einschalten nur bei sicher bekanntem ausgeschaltetem Zustand möglich')
        if a.action=='ON': device.update(pending=True,pending_since=time.monotonic(),command_notice=None)
        else: device['command_notice']=None
        # MQTT QoS 1 is at-least-once and can redeliver an ON, extending
        # PulseTime. ON therefore uses QoS 0; OFF may safely be retried.
        result=client.publish(BASE+'/cmnd/POWER',a.action,qos=0 if a.action=='ON' else 1,retain=False)
        if result.rc:
            if a.action=='ON': device.update(pending=False,pending_since=None)
            raise HTTPException(503,'Befehl konnte nicht gesendet werden')
    return {'sent':True,'message':'Befehl gesendet; auf Rückmeldung der Steckdose warten'}

class LightMode(BaseModel): all_day:StrictBool

@app.get('/api/light-mode')
def light_mode():
    with LOCK:
        value=device['light_mode']
    return {'all_day':True if value=='ON' else False if value=='OFF' else None}

@app.put('/api/light-mode')
def set_light_mode(payload:LightMode):
    if not client or not client.is_connected(): raise HTTPException(503,'MQTT-Verbindung nicht verfügbar')
    with LOCK:
        if device['light_mode'] not in ('ON','OFF'): raise HTTPException(503,'Schaltmodus noch nicht verfügbar')
        result=client.publish(BASE+'/config/ganztags','ON' if payload.all_day else 'OFF',qos=1,retain=True)
        if result.rc: raise HTTPException(503,'Einstellung konnte nicht gesendet werden')
    return {'sent':True}

class Tariff(BaseModel): euros_per_kwh:float=Field(ge=0,le=10,allow_inf_nan=False)

@app.get('/api/settings')
def get_settings(): return {'euros_per_kwh':read_tariff()}

@app.put('/api/settings')
def put_settings(payload:Tariff):
    with closing(settings_db()) as db:
        with db: db.execute("INSERT INTO settings(key,value) VALUES('tariff',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(payload.euros_per_kwh),))
    return get_settings()

def parse_history_start(value):
    """Require a timezone-aware ISO-8601 instant; normalize to UTC."""
    if not value: raise ValueError('history start missing')
    try: parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError: raise ValueError('invalid history start') from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('history start requires timezone')
    return parsed.astimezone(timezone.utc)

def history_bounds(start,end,cutoff):
    if cutoff>=end:return None,None
    query_start=max(start,cutoff)
    return query_start,max(cutoff,query_start-timedelta(seconds=600))

@app.get('/api/history')
def history(days:int=1, until:str|None=None):
    if days not in (1,7,30,90): raise HTTPException(422,'Zeitraum nicht verfügbar')
    try: cutoff=parse_history_start(os.getenv('HEATER_HISTORY_START',''))
    except ValueError: raise HTTPException(503,'Erfassungsbeginn ist nicht gültig eingerichtet') from None
    try:
        try: start,now=history_window(days,until,datetime.now(timezone.utc))
        except ValueError: raise HTTPException(422,'Ungültiges oder zukünftiges Datum') from None
        query_start,observation_start=history_bounds(start,now,cutoff)
        prior=[];rows=[];observed=[];status_samples=[]
        if query_start is not None:
            dsn=os.getenv('HEATER_PG_DSN')
            if not dsn: raise HTTPException(503,'Archivverbindung nicht eingerichtet')
            import psycopg2
            power_topics=[x+'/stat/POWER' for x in HISTORY]; lwt_topics=[x+'/tele/LWT' for x in HISTORY]
            sensor_topics=[x+'/tele/SENSOR' for x in HISTORY]
            status_topics=[x+'/stat/STATUS' for x in HISTORY]
            with psycopg2.connect(dsn,connect_timeout=4,options='-c statement_timeout=8000') as db:
                with db.cursor() as cur:
                    cur.execute('SELECT ts,topic,payload FROM events WHERE topic=ANY(%s) AND ts >= %s AND ts < %s ORDER BY ts DESC LIMIT 1',(power_topics+lwt_topics,cutoff,query_start))
                    prior=cur.fetchall()
                    cur.execute('SELECT ts,topic,payload FROM events WHERE topic=ANY(%s) AND ts >= %s AND ts <= %s ORDER BY ts ASC LIMIT 20001',(power_topics+lwt_topics,query_start,now))
                    rows=cur.fetchall()
                    if len(rows)>20000: raise HTTPException(503,'Zu viele Ereignisse für diesen Zeitraum')
                    cur.execute("SELECT ts,topic,value FROM samples WHERE topic=ANY(%s) AND field='ENERGY.Total' AND ts >= %s AND ts <= %s ORDER BY ts ASC LIMIT 50001",(sensor_topics,observation_start,now))
                    observed=cur.fetchall()
                    if len(observed)>50000: raise HTTPException(503,'Zu viele Messwerte für diesen Zeitraum')
                    cur.execute("SELECT ts,topic,value FROM samples WHERE topic=ANY(%s) AND field='Status.Power' AND ts >= %s AND ts <= %s ORDER BY ts ASC LIMIT 20001",(status_topics,observation_start,now))
                    status_samples=cur.fetchall()
                    if len(status_samples)>20000: raise HTTPException(503,'Zu viele Statusmeldungen für diesen Zeitraum')
        events=normalize_events(prior+rows)+normalize_status_samples(status_samples)
        result=calculate_intervals(start,now,events,[row[0] for row in observed])
        energy=[row for row in observed if query_start is not None and row[0]>=query_start]
        # HISTORY is explicitly configured as aliases for the same physical
        # device, so merge its monotonic cumulative readings chronologically.
        result.update(merge_energy_topics(energy))
        result['first_energy_at']=energy[0][0].isoformat() if energy else None
        result['last_energy_at']=energy[-1][0].isoformat() if energy else None
        result['tariff']=read_tariff()
        result['estimated_cost']=round(result['kwh']*result['tariff'],2) if result['kwh'] is not None and result['tariff'] is not None else None
        result['start']=start.isoformat();result['end']=now.isoformat()
        result['history_start']=cutoff.isoformat()
        return result
    except HTTPException: raise
    except Exception as exc:
        print('history error:',type(exc).__name__,flush=True)
        raise HTTPException(503,'Archiv derzeit nicht erreichbar') from None
