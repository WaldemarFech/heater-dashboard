import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import monitor

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

class MonitorTests(unittest.TestCase):
    def test_both_exact_topics_are_required_and_fresh(self):
        self.assertEqual(monitor.assess({topic: NOW for topic in monitor.TOPICS}, NOW, 180), [])
        with self.assertRaises(ValueError): monitor.assess({}, NOW.replace(tzinfo=None), 180)
    def test_missing_and_stale_topic_are_failures(self):
        data = {monitor.TOPICS[0]: NOW - timedelta(seconds=181)}
        self.assertEqual(len(monitor.assess(data, NOW, 180)), 2)
    def test_database_outage_is_failure_without_dsn_leak(self):
        with patch.object(monitor, 'fetch_latest', side_effect=RuntimeError('secret-dsn')):
            with self.assertLogs('heater-monitor', 'ERROR') as log:
                self.assertFalse(monitor.check('secret-dsn', now=NOW))
        self.assertNotIn('secret-dsn', ''.join(log.output))
    def test_recovery_after_outage(self):
        data = {topic: NOW for topic in monitor.TOPICS}
        with patch.object(monitor, 'fetch_latest', side_effect=[OSError(), data]):
            self.assertFalse(monitor.check('dsn', now=NOW))
            self.assertTrue(monitor.check('dsn', now=NOW))
    def test_future_timestamps_are_unhealthy(self):
        data = {topic: NOW + timedelta(seconds=5) for topic in monitor.TOPICS}
        self.assertEqual(len(monitor.assess(data, NOW, 180)), 2)
