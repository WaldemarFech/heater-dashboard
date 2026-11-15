"""Read-only freshness monitor; never controls the recorder or the device."""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from recorder import TOPICS

LOG = logging.getLogger('heater-monitor')
MAX_AGE = 180
PERIOD = 60
START_GRACE = 10

def assess(latest, now, max_age):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('UTC-aware now required')
    problems = []
    for topic in TOPICS:
        seen = latest.get(topic)
        if seen is None:
            problems.append((topic, 'missing or stale'))
        elif seen.tzinfo is None or seen.utcoffset() is None:
            problems.append((topic, 'timestamp lacks timezone'))
        else:
            age = (now - seen).total_seconds()
            if age < 0 or age > max_age:
                problems.append((topic, 'future or stale timestamp'))
    return problems

def fetch_latest(dsn, now, max_age):
    import psycopg2
    # Time bound enables Timescale chunk pruning; at most the most recent
    # 180 seconds need examining, rather than years of compressed history.
    with psycopg2.connect(dsn, connect_timeout=5, options='-c statement_timeout=5000 -c default_transaction_read_only=on') as db:
        with db.cursor() as cursor:
            cursor.execute('SELECT topic, max(received_at) FROM heater_raw_messages WHERE topic = ANY(%s) AND received_at >= %s GROUP BY topic', (list(TOPICS), now - timedelta(seconds=max_age)))
            return dict(cursor.fetchall())

def check(dsn, now=None, max_age=MAX_AGE):
    now = now or datetime.now(timezone.utc)
    try:
        latest = fetch_latest(dsn, now, max_age)
        problems = assess(latest, now, max_age)
    except Exception as exc:
        # Exception messages can contain credentials or DSNs.
        LOG.error('DB freshness check failed (%s)', type(exc).__name__)
        return False
    for topic, reason in problems:
        LOG.error('Topic %s: %s', topic, reason)
    if problems: return False
    LOG.info('Both topics have fresh records')
    return True

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    dsn = os.getenv('HEATER_MONITOR_PG_DSN')
    if not dsn:
        LOG.error('HEATER_MONITOR_PG_DSN is not configured')
        return 2
    time.sleep(START_GRACE)
    while True:
        if not check(dsn): return 1
        time.sleep(PERIOD)

if __name__ == '__main__':
    raise SystemExit(main())
