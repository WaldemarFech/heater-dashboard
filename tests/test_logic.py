import unittest
from datetime import datetime, timedelta, timezone
from heater_logic import calculate_intervals, energy_delta, merge_energy_topics, normalize_events, validate_turn_on

UTC=timezone.utc
T=datetime(2026,11,8,0,0,tzinfo=UTC)
def beats(*minutes): return [T+timedelta(minutes=m) for m in minutes]

class Behavior(unittest.TestCase):
 def test_fresh_off_is_the_only_turn_on_state(self):
  for state,online,age,pending in [('ON',True,1,False),('OFF',False,1,False),('OFF',True,31,False),('unknown',True,1,False),('OFF',True,1,True)]:
   self.assertFalse(validate_turn_on(state,online,age,pending))
  self.assertTrue(validate_turn_on('OFF',True,2,False))

 def test_intervals_stop_counting_at_offline_and_unknown_after_online(self):
  events=[(T,'OFF','power'),(T+timedelta(minutes=2),'ON','power'),(T+timedelta(minutes=5),'Offline','lwt'),(T+timedelta(minutes=7),'Online','lwt'),(T+timedelta(minutes=8),'OFF','power')]
  result=calculate_intervals(T,T+timedelta(minutes=10),events,beats(0,5,10))
  self.assertEqual(result['on_seconds'],180)
  self.assertEqual(result['unknown_seconds'],180)
  self.assertEqual(result['starts'],1)

 def test_running_at_window_boundary_is_not_a_new_start(self):
  prior=[(T-timedelta(minutes=5),'x/stat/POWER','ON')]
  within=[(T+timedelta(minutes=4),'x/stat/POWER','OFF')]
  result=calculate_intervals(T,T+timedelta(minutes=10),normalize_events(prior+within),beats(-10,-5,0,5,10))
  self.assertEqual(result['starts'],0)
  self.assertEqual(result['on_seconds'],240)

 def test_offline_before_boundary_and_online_inside_remains_unknown_until_power(self):
  prior=[(T-timedelta(minutes=1),'x/tele/LWT','Offline')]
  within=[(T+timedelta(minutes=1),'x/tele/LWT','Online'),(T+timedelta(minutes=3),'x/stat/POWER','ON')]
  result=calculate_intervals(T,T+timedelta(minutes=5),normalize_events(prior+within),beats(-5,0,5))
  self.assertEqual(result['unknown_seconds'],180)
  self.assertEqual(result['on_seconds'],120)
  self.assertEqual(result['starts'],0)

 def test_missing_archive_coverage_during_on_is_unknown_after_outage(self):
  events=[(T,'OFF','power'),(T+timedelta(minutes=1),'ON','power'),(T+timedelta(minutes=26),'OFF','power')]
  result=calculate_intervals(T,T+timedelta(minutes=30),events,beats(0,5,25,30))
  self.assertEqual(result['on_seconds'],240)
  self.assertEqual(result['unknown_seconds'],1260)
  self.assertEqual(result['starts'],1)

 def test_coverage_edges_are_unknown(self):
  events=[(T,'OFF','power'),(T+timedelta(minutes=3),'ON','power')]
  result=calculate_intervals(T,T+timedelta(minutes=10),events,beats(2,5,8))
  self.assertEqual(result['unknown_seconds'],300)
  self.assertEqual(result['on_seconds'],300)
  self.assertEqual(result['starts'],0)

 def test_status_event_in_heartbeat_gap_cannot_prove_restart(self):
  events=[(T,'OFF','power'),(T+timedelta(minutes=10),'ON','power'),(T+timedelta(minutes=21),'OFF','power'),(T+timedelta(minutes=22),'ON','power')]
  result=calculate_intervals(T,T+timedelta(minutes=25),events,beats(0,5,20,25))
  self.assertEqual(result['starts'],1)
  self.assertEqual(result['on_seconds'],180)
  self.assertEqual(result['unknown_seconds'],960)

 def test_no_heartbeat_means_no_claimed_running_time(self):
  result=calculate_intervals(T,T+timedelta(minutes=10),[(T,'ON','power')],[])
  self.assertEqual(result['unknown_seconds'],600)
  self.assertEqual(result['on_seconds'],0)

 def test_energy_requires_two_samples_and_does_not_hide_resets(self):
  self.assertIsNone(energy_delta([3.0])['kwh'])
  self.assertEqual(energy_delta([1,1.5,2])['kwh'],1)
  self.assertTrue(energy_delta([4,5,0.2,0.4])['reset'])
  self.assertIsNone(energy_delta([4,5,0.2,0.4])['kwh'])

 def test_same_meter_topic_migration_preserves_cumulative_counter(self):
  values=[(T,'old',1.0),(T+timedelta(seconds=10),'old',1.1),(T+timedelta(seconds=20),'new',1.1),(T+timedelta(seconds=30),'new',1.4)]
  self.assertAlmostEqual(merge_energy_topics(values)['kwh'],0.4)
  values.append((T+timedelta(seconds=40),'new',0.2))
  self.assertIsNone(merge_energy_topics(values)['kwh'])

 def test_energy_gap_is_reported_even_with_increasing_counter(self):
  result=merge_energy_topics([(T,'old',1.0),(T+timedelta(minutes=30),'new',1.1)])
  self.assertEqual(result['gap_count'],1)
  self.assertAlmostEqual(result['kwh'],0.1)

if __name__=='__main__': unittest.main()


def test_history_window_supports_archived_days_and_berlin_dst():
    from datetime import datetime, timezone
    from heater_logic import history_window
    now=datetime(2026,11,8,12,tzinfo=timezone.utc)
    start,end=history_window(1,'2026-03-29',now)
    assert start==datetime(2026,3,28,23,tzinfo=timezone.utc)
    assert end==datetime(2026,3,29,22,tzinfo=timezone.utc)
    start,end=history_window(7,'2025-01-15',now)
    assert start==datetime(2025,1,8,23,tzinfo=timezone.utc)
    assert end==datetime(2025,1,15,23,tzinfo=timezone.utc)


def test_history_window_rejects_invalid_and_future_dates():
    from datetime import datetime, timezone
    import pytest
    from heater_logic import history_window
    now=datetime(2026,11,8,12,tzinfo=timezone.utc)
    for date in ('not-a-date','2026-02-30','2027-01-01'):
        with pytest.raises(ValueError): history_window(1,date,now)
    start,end=history_window(1,'2026-11-08',now)
    assert end==now


def test_status_power_samples_provide_only_valid_single_relay_states():
    from heater_logic import normalize_status_samples
    rows=[(T,'status',1.0),(T+timedelta(seconds=1),'status',0.0),(T+timedelta(seconds=2),'status',2.0),(T+timedelta(seconds=3),'status',None)]
    assert normalize_status_samples(rows)==[(T,'ON','power'),(T+timedelta(seconds=1),'OFF','power')]

def test_status_sample_restores_known_state_after_reconnect():
    from heater_logic import normalize_status_samples
    events=[(T,'OFF','power'),(T+timedelta(minutes=1),'ON','power'),(T+timedelta(minutes=2),'Offline','lwt'),(T+timedelta(minutes=2,seconds=1),'Online','lwt')]
    events+=normalize_status_samples([(T+timedelta(minutes=3),'status',1.0)])
    events.append((T+timedelta(minutes=10),'OFF','power'))
    result=calculate_intervals(T,T+timedelta(minutes=11),events,beats(0,1,2,3,4,5,6,7,8,9,10,11))
    assert result['on_seconds']==8*60
