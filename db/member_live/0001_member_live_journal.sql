-- RHEN Cloud member order ledger: isolated migration, NEVER in the owner RHEN
-- foundation migrator path. Apply only to a dedicated RHEN Cloud database.
-- No application worker is allowed to grant trading authority by running DDL.
CREATE SCHEMA IF NOT EXISTS member_live;

CREATE TABLE IF NOT EXISTS member_live.order_intents (
  member_id text NOT NULL CHECK(length(member_id) BETWEEN 1 AND 128),
  connection_id text NOT NULL CHECK(length(connection_id) BETWEEN 1 AND 128),
  broker_account_id text NOT NULL CHECK(length(broker_account_id) BETWEEN 1 AND 128),
  signal_id text NOT NULL CHECK(length(signal_id) BETWEEN 1 AND 128),
  digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
  client_order_id text NOT NULL UNIQUE CHECK(client_order_id ~ '^rhcl-[a-f0-9]{46}$'),
  state text NOT NULL CHECK(state IN ('reserved','uncertain','confirmed','blocked')),
  broker_order_id text,
  broker_status text,
  created_at bigint NOT NULL CHECK(created_at > 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(member_id, connection_id, signal_id)
);
CREATE INDEX IF NOT EXISTS order_intents_owner_idx
  ON member_live.order_intents(member_id, connection_id, created_at DESC);

-- A separate, explicitly authorized reconciliation process is required
-- before closing/releasing order reservations. Do NOT blindly retry 'uncertain'.
