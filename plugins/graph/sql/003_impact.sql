-- 003_impact.sql: predicted versus actual impact. Predictions are PROPOSALS: nothing here
-- changes ledger state. Test ids are pytest node ids ("tests/test_a.py::test_x"); a bare path
-- ("tests/test_a.py") predicts the whole file.

CREATE TABLE IF NOT EXISTS prediction (
  id                bigserial PRIMARY KEY,
  node_id           uuid REFERENCES node(id) ON DELETE CASCADE,
  snapshot_id       bigint NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
  predicted_tests   text[] NOT NULL DEFAULT '{}',
  predicted_routes  text[] NOT NULL DEFAULT '{}',
  inputs            jsonb  NOT NULL DEFAULT '{}'::jsonb,   -- {"paths":[...],"symbols":[...],"depth":4}
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prediction_node_idx ON prediction (node_id, created_at);

-- One outcome per prediction; recording again replaces it (upsert on prediction_id).
CREATE TABLE IF NOT EXISTS prediction_outcome (
  prediction_id         bigint PRIMARY KEY REFERENCES prediction(id) ON DELETE CASCADE,
  actual_failing_tests  text[] NOT NULL DEFAULT '{}',
  precision             numeric,        -- null when nothing was predicted
  recall                numeric,        -- null when nothing failed
  recorded_at           timestamptz NOT NULL DEFAULT now()
);
