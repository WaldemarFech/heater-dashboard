import json
import unittest
from types import SimpleNamespace
import recorder

class Tests(unittest.TestCase):
    def setUp(self):
        self.rec = recorder.Recorder(max_queue=2)
        self.message = SimpleNamespace(topic=recorder.TOPICS[0], payload=b'{"POWER":"OFF"}', retain=False)
    def test_identical_messages_are_kept(self):
        self.rec.on_message(None, None, self.message)
        self.rec.on_message(None, None, self.message)
        a, b = self.rec.queue.get_nowait(), self.rec.queue.get_nowait()
        self.assertEqual(a[1:3], b[1:3])
        self.assertNotEqual(a[3], b[3])
        self.assertEqual(json.loads(a[2]), {"POWER":"OFF"})
    def test_reconnect_subscribes_exact_topics(self):
        class Client:
            def __init__(self): self.calls=[]
            def subscribe(self, topics): self.calls.append(topics); return (0, 1)
        c = Client()
        self.rec.on_connect(c, None, None, 0, None)
        self.rec.on_connect(c, None, None, 0, None)
        self.assertEqual(c.calls, [[(x, 0) for x in recorder.TOPICS]] * 2)
    def test_malformed_or_retained_rejected(self):
        self.message.payload=b'{bad'
        self.rec.on_message(None,None,self.message)
        self.message.payload=b'{}'; self.message.retain=True
        self.rec.on_message(None,None,self.message)
        self.assertTrue(self.rec.queue.empty())
        self.assertEqual(self.rec.invalid, 1)
    def test_queue_overflow_is_counted(self):
        for _ in range(3): self.rec.on_message(None,None,self.message)
        self.assertEqual(self.rec.queue.qsize(),2)
        self.assertEqual(self.rec.dropped,1)
    def test_db_failure_retries_same_message(self):
        self.rec.on_message(None,None,self.message)
        record=self.rec.queue.get_nowait()
        class Store:
            def __init__(self): self.calls=0
            def write(self, item):
                self.calls+=1
                if self.calls==1: raise OSError('down')
                self.saved=item
        store=Store()
        self.rec.write_with_retry(store, record, sleep=lambda _:None)
        self.assertEqual(store.calls,2)
        self.assertEqual(store.saved,record)

if __name__=='__main__': unittest.main()


class SchemaTests(unittest.TestCase):
    def test_schema_creates_seven_day_hypertable_without_retention(self):
        from pathlib import Path
        sql = Path('schema.sql').read_text(encoding='utf-8-sig').lower()
        self.assertIn('primary key (received_at, message_id)', sql)
        self.assertIn("create_hypertable('heater_raw_messages', 'received_at'", sql)
        self.assertIn("chunk_time_interval => interval '7 days'", sql)
        self.assertIn("add_compression_policy('heater_raw_messages', interval '7 days'", sql)
        self.assertNotIn('add_retention_policy', sql)
    def test_insert_conflict_key_includes_time(self):
        from pathlib import Path
        source=Path('recorder.py').read_text(encoding='utf-8-sig').lower()
        self.assertIn('on conflict (received_at, message_id) do nothing', source)

class ResourceLimitTests(unittest.TestCase):
 def test_queue_default_and_override_are_capped(self):
  self.assertEqual(recorder.Recorder().queue.maxsize, 2000)
  with self.assertRaises(ValueError): recorder.Recorder(max_queue=2001)
  with self.assertRaises(ValueError): recorder.Recorder(max_queue=0)
 def test_payload_limit_accepts_boundary_and_rejects_larger(self):
  rec=recorder.Recorder(max_queue=2)
  payload=b'{"x":"' + b'a' * (4096-8) + b'"}'
  self.assertEqual(len(payload),4096)
  msg=SimpleNamespace(topic=recorder.TOPICS[0],payload=payload,retain=False)
  rec.on_message(None,None,msg)
  msg.payload=b'{"x":"' + b'a' * (4097-8) + b'"}'
  rec.on_message(None,None,msg)
  self.assertEqual(rec.queue.qsize(),1)
  self.assertEqual(rec.invalid,1)
