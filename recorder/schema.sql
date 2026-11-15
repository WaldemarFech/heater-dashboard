-- Fresh-install migration; run as an administrative owner with TimescaleDB installed.
-- Deliberately fails if the table already exists: review migration separately.
BEGIN;
CREATE TABLE heater_raw_messages (
    received_at timestamptz NOT NULL,
    message_id uuid NOT NULL,
    topic text NOT NULL,
    payload json NOT NULL,
    PRIMARY KEY (received_at, message_id)
);
SELECT create_hypertable('heater_raw_messages', 'received_at', chunk_time_interval => INTERVAL '7 days');
CREATE INDEX heater_raw_messages_time_idx ON heater_raw_messages (received_at, topic);
ALTER TABLE heater_raw_messages SET (timescaledb.compress, timescaledb.compress_segmentby = 'topic', timescaledb.compress_orderby = 'received_at DESC');
SELECT add_compression_policy('heater_raw_messages', INTERVAL '7 days');
COMMIT;
-- Provision a dedicated LOGIN role separately with a securely generated password.
-- GRANT USAGE ON SCHEMA public TO heater_recorder;
-- GRANT INSERT, SELECT ON heater_raw_messages TO heater_recorder;
-- TimescaleDB 2.28.3: column-level grants fail on compressed hypertables.
-- For a separate monitoring role with default_transaction_read_only=on, provision separately:
-- GRANT USAGE ON SCHEMA public TO heater_monitor;
-- GRANT SELECT ON heater_raw_messages TO heater_monitor;
