import unittest,time
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
import app

class FakeMQTT:
 def __init__(self):self.calls=[]
 def is_connected(self):return True
 def publish(self,*args,**kwargs):self.calls.append((args,kwargs));return SimpleNamespace(rc=0)

class CommandTests(unittest.TestCase):
 def test_lost_on_released_only_after_new_telemetry_off_past_timeout(self):
  c=FakeMQTT()
  with patch.object(app,'client',c),patch.object(app.time,'monotonic',return_value=100),patch.dict(app.device,{'online':True,'state':'OFF','updated':time.time(),'pending':False,'pending_since':None,'command_notice':None}):
   app.power(app.Action(action='ON'))
   app.record_power('OFF','telemetry',now=1000,monotonic=114)
   self.assertTrue(app.device['pending'])
   app.record_power('OFF','stat',now=1001,monotonic=120)
   self.assertTrue(app.device['pending'])
   app.record_power('OFF','telemetry',now=1002,monotonic=120)
   self.assertFalse(app.device['pending'])
   self.assertIn('Keine Einschaltbestätigung',app.device['command_notice'])
   self.assertEqual(len(c.calls),1)  # no automatic replay
 def test_delayed_on_ack_wins_over_timeout(self):
  with patch.dict(app.device,{'online':True,'state':'OFF','updated':time.time(),'pending':True,'pending_since':100,'command_notice':None}):
   app.record_power('ON','stat',now=1001,monotonic=125)
   self.assertFalse(app.device['pending'])
   self.assertEqual(app.device['state'],'ON')
   self.assertIn('bestätigt',app.device['command_notice'])
 def test_on_arriving_after_off_timeout_is_reflected_and_not_retried(self):
  with patch.dict(app.device,{'online':True,'state':'OFF','updated':time.time(),'pending':True,'pending_since':100,'command_notice':None}):
   app.record_power('OFF','telemetry',now=1001,monotonic=121)
   self.assertIn('Keine Einschaltbestätigung',app.device['command_notice'])
   app.record_power('ON','stat',now=1002,monotonic=122)
   self.assertEqual(app.device['state'],'ON')
   self.assertFalse(app.device['pending'])
   self.assertIn('bestätigt',app.device['command_notice'])
 def test_offline_state_never_enables_on(self):
  with patch.dict(app.device,{'online':True,'state':'OFF','updated':time.time(),'pending':True,'pending_since':100}):
   app.record_offline()
   self.assertFalse(app.state()['can_turn_on'])
   self.assertEqual(app.state()['state'],'unknown')

 def test_no_second_on_while_waiting_for_device_ack(self):
  c=FakeMQTT()
  with patch.object(app,'client',c),patch.dict(app.device,{'online':True,'state':'OFF','updated':time.time(),'pending':False}):
   self.assertTrue(app.power(app.Action(action='ON'))['sent'])
   with self.assertRaises(HTTPException) as err:app.power(app.Action(action='ON'))
   self.assertEqual(err.exception.status_code,409)
   self.assertEqual(len(c.calls),1)
   self.assertEqual(c.calls[0][0][1],'ON')
   self.assertFalse(c.calls[0][1]['retain'])
   self.assertEqual(c.calls[0][1]['qos'],0)
 def test_off_allowed_when_state_is_unknown(self):
  c=FakeMQTT()
  with patch.object(app,'client',c),patch.dict(app.device,{'online':False,'state':'unknown','updated':None,'pending':False}):
   self.assertTrue(app.power(app.Action(action='OFF'))['sent'])
   self.assertEqual(c.calls[0][0][1],'OFF')
   self.assertEqual(c.calls[0][1]['qos'],1)

if __name__=='__main__':unittest.main()

class LightModeTests(unittest.TestCase):
 def test_mode_publishes_retained_configuration_not_power(self):
  c=FakeMQTT()
  with patch.object(app,'client',c),patch.dict(app.device,{'light_mode':'ON'}):
   self.assertTrue(app.set_light_mode(app.LightMode(all_day=False))['sent'])
   self.assertEqual(c.calls[0][0][0],app.BASE+'/config/ganztags')
   self.assertEqual(c.calls[0][0][1],'OFF')
   self.assertEqual(c.calls[0][1],{'qos':1,'retain':True})
 def test_mode_cannot_change_when_unconfirmed(self):
  c=FakeMQTT()
  with patch.object(app,'client',c),patch.dict(app.device,{'light_mode':None}):
   with self.assertRaises(HTTPException) as err:app.set_light_mode(app.LightMode(all_day=True))
   self.assertEqual(err.exception.status_code,503)
   self.assertFalse(c.calls)
