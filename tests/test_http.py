import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path

class HttpContracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory()
  with socket.socket() as sock:
   sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1]
  cls.base=f'http://127.0.0.1:{cls.port}'
  env=os.environ.copy();env['HEATER_DATA_DIR']=cls.tmp.name;env['HEATER_PUBLIC_ORIGIN']=cls.base
  for name in ('HEATER_MQTT_HOST','HEATER_MQTT_USER','HEATER_MQTT_PASSWORD','HEATER_PG_DSN'): env.pop(name,None)
  cls.process=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(cls.port)],cwd=Path(__file__).resolve().parents[1],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  for _ in range(80):
   try:
    with urllib.request.urlopen(cls.base+'/api/state',timeout=.2):break
   except (OSError,urllib.error.URLError):time.sleep(.1)
  else: raise RuntimeError('Server did not start')
 @classmethod
 def tearDownClass(cls):
  cls.process.terminate();cls.process.wait(timeout=5)
  for attempt in range(10):
   try:cls.tmp.cleanup();break
   except PermissionError:
    if attempt==9:raise
    time.sleep(.2)
 def request(self,path,method='GET',data=None,origin=None,host=None,csrf=True):
  headers={};
  if data is not None: headers['Content-Type']='application/json';data=json.dumps(data).encode()
  if origin:headers['Origin']=origin
  if host:headers['Host']=host
  if method not in ('GET','HEAD') and csrf:headers['X-Heater-Request']='1'
  try:
   with urllib.request.urlopen(urllib.request.Request(self.base+path,headers=headers,data=data,method=method),timeout=3) as r:return r.status,json.loads(r.read())
  except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
 def test_unknown_state_cannot_enable_device(self):
  status,body=self.request('/api/state');self.assertEqual(status,200);self.assertEqual(body['state'],'unknown');self.assertFalse(body['can_turn_on'])
  status,_=self.request('/api/power','POST',{'action':'ON'},self.base);self.assertEqual(status,503)
 def test_cross_origin_rejected_and_tariff_persisted(self):
  status,_=self.request('/api/settings','PUT',{'euros_per_kwh':0.32},'https://evil.example');self.assertEqual(status,403)
  status,_=self.request('/api/settings','PUT',{'euros_per_kwh':0.32},self.base,csrf=False);self.assertEqual(status,403)
  status,_=self.request('/api/settings','PUT',{'euros_per_kwh':0.32},self.base,host='evil.example');self.assertEqual(status,400)
  status,_=self.request('/api/settings','PUT',{'euros_per_kwh':0.32},self.base);self.assertEqual(status,200)
  status,body=self.request('/api/settings');self.assertEqual(body['euros_per_kwh'],0.32)
 def test_unavailable_archive_is_not_reported_as_zero(self):
  status,_=self.request('/api/history?days=1');self.assertEqual(status,503)
 def test_ui_identifies_estimated_runtime_and_short_outage_limit(self):
  with urllib.request.urlopen(self.base+'/',timeout=3) as response: html=response.read().decode('utf-8')
  self.assertIn('Geschätzte Laufzeit',html)
  self.assertIn('Erfasste Einschaltungen',html)
  self.assertIn('Auch kürzere Unterbrechungen',html)

 def test_database_status_fails_closed_when_unconfigured(self):
  status,body=self.request('/api/database-status');self.assertEqual(status,503);self.assertNotIn('password',str(body).lower())
 def test_mode_write_requires_same_origin(self):
  status,_=self.request('/api/light-mode','PUT',{'all_day':True},'https://evil.example');self.assertEqual(status,403)

if __name__=='__main__':unittest.main()
