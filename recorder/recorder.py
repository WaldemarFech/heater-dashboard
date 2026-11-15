"""Subscribe-only heater raw recorder. No network access occurs on import."""
import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

BASE = os.getenv('HEATER_MQTT_BASE', 'home/heater').rstrip('/') + '/tele/'
TOPICS = (BASE + 'STATE', BASE + 'SENSOR')
MAX_PAYLOAD = 4096
MAX_QUEUE = 2000
LOG = logging.getLogger('heater-recorder')

class Recorder:
    def __init__(self, max_queue=MAX_QUEUE):
        if not 1 <= max_queue <= MAX_QUEUE: raise ValueError('max_queue must be between 1 and 2000')
        self.queue = queue.Queue(maxsize=max_queue)
        self.dropped = 0
        self.invalid = 0
        self.stop = threading.Event()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            result, _ = client.subscribe([(topic, 0) for topic in TOPICS])
            if result != 0: LOG.error('MQTT subscribe failed rc=%s', result)
            else: LOG.info('MQTT connected; subscriptions requested')
        else:
            LOG.error('MQTT connect rejected reason=%s', reason_code)

    def on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if any(code.is_failure for code in reason_codes):
            LOG.error('MQTT subscription rejected: %s', reason_codes)
        else:
            LOG.info('MQTT subscriptions acknowledged')

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        LOG.warning('MQTT disconnected reason=%s; messages while disconnected may be lost', reason_code)

    def on_message(self, client, userdata, message):
        if message.retain or message.topic not in TOPICS: return
        received_at = datetime.now(timezone.utc)
        try:
            if len(message.payload) > MAX_PAYLOAD: raise ValueError('oversized')
            payload = message.payload.decode('utf-8')
            parsed = json.loads(payload, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(parsed, dict): raise ValueError('JSON object required')
        except (UnicodeError, ValueError, RecursionError):
            self.invalid += 1
            LOG.error('Rejected malformed or oversized message topic=%s total_invalid=%d', message.topic, self.invalid)
            return
        item = (received_at, message.topic, payload, uuid.uuid4())
        try: self.queue.put_nowait(item)
        except queue.Full:
            self.dropped += 1
            LOG.error('QUEUE FULL: message lost topic=%s total_dropped=%d', message.topic, self.dropped)

    def write_with_retry(self, store, item, sleep=time.sleep):
        while not self.stop.is_set():
            try:
                store.write(item)
                return True
            except Exception as exc:
                LOG.error('DB write failed (%s); retrying; queued=%d', type(exc).__name__, self.queue.qsize())
                sleep(2)
        LOG.error('Shutdown with uncommitted message and %d queued messages; data may be lost', self.queue.qsize())
        return False

    def run_writer(self, store):
        while not self.stop.is_set():
            try: item = self.queue.get(timeout=1)
            except queue.Empty: continue
            try: self.write_with_retry(store, item)
            finally: self.queue.task_done()

class PostgresStore:
    def __init__(self, dsn):
        self.dsn = dsn
        self.connection = None
    def write(self, item):
        import psycopg2
        try:
            if self.connection is None or self.connection.closed:
                self.connection = psycopg2.connect(self.dsn, connect_timeout=5, options='-c statement_timeout=5000')
            with self.connection:
                with self.connection.cursor() as cursor:
                    cursor.execute('INSERT INTO heater_raw_messages (received_at, topic, payload, message_id) VALUES (%s,%s,%s::json,%s) ON CONFLICT (received_at, message_id) DO NOTHING', (item[0], item[1], item[2], str(item[3])))
        except Exception:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            raise

def main():
    import paho.mqtt.client as mqtt
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    for key in ('HEATER_MQTT_HOST', 'HEATER_MQTT_USER', 'HEATER_MQTT_PASSWORD', 'HEATER_PG_DSN'):
        if not os.getenv(key): raise SystemExit('Missing required environment variable: ' + key)
    rec = Recorder(max_queue=int(os.getenv('HEATER_QUEUE_SIZE', '2000')))
    writer = threading.Thread(target=rec.run_writer, args=(PostgresStore(os.environ['HEATER_PG_DSN']),), daemon=True)
    writer.start()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='heater-raw-' + uuid.uuid4().hex, clean_session=True)
    client.username_pw_set(os.environ['HEATER_MQTT_USER'], os.environ['HEATER_MQTT_PASSWORD'])
    client.on_connect, client.on_disconnect, client.on_message = rec.on_connect, rec.on_disconnect, rec.on_message
    client.on_subscribe = rec.on_subscribe
    client.reconnect_delay_set(2, 60)
    try:
        client.connect_async(os.environ['HEATER_MQTT_HOST'], int(os.getenv('HEATER_MQTT_PORT', '1883')), 30)
        client.loop_start()
        while True:
            time.sleep(60)
            LOG.info('health queued=%d invalid=%d dropped=%d mqtt_connected=%s', rec.queue.qsize(), rec.invalid, rec.dropped, client.is_connected())
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        rec.stop.set()
        writer.join(timeout=8)
        if writer.is_alive() or rec.queue.qsize(): LOG.error('Shutdown with %d queued messages; data may be lost', rec.queue.qsize())

if __name__ == '__main__': main()
