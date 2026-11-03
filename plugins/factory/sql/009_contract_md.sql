-- 009_contract_md.sql: the contract.md a contract node was drafted or last updated from (ledger.contract_md parses
-- it; intake.contract_draft and intake.contract_update store it). Null for nodes drafted without one and for every
-- other kind. Idempotent.

ALTER TABLE node ADD COLUMN IF NOT EXISTS contract_md text;
