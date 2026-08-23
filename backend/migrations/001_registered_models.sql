-- Splits monitored_models (previously: one row per account+deployment, each
-- carrying its own copy of model name/pricing) into:
--   - registered_models: each distinct model name registered exactly once
--     with its pricing
--   - monitored_models: now just a link from an account's deployment to a
--     registered model, with no pricing/name of its own
--
-- Safe to run once against a database created by the pre-registry schema.
-- Preserves monitored_models.id (and therefore all usage_snapshots history).

BEGIN;

CREATE TABLE IF NOT EXISTS registered_models (
    id SERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    input_price_per_million DOUBLE PRECISION NOT NULL,
    cached_input_price_per_million DOUBLE PRECISION NOT NULL DEFAULT 0,
    output_price_per_million DOUBLE PRECISION NOT NULL,
    currency VARCHAR(8) NOT NULL DEFAULT 'USD',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_registered_models_name ON registered_models (name);

-- One registry row per distinct model_name; when a name was priced
-- differently across accounts (shouldn't normally happen), the most
-- recently saved price wins.
INSERT INTO registered_models (name, input_price_per_million, cached_input_price_per_million, output_price_per_million, currency, created_at, updated_at)
SELECT DISTINCT ON (model_name)
    model_name,
    input_price_per_million,
    cached_input_price_per_million,
    output_price_per_million,
    currency,
    created_at,
    now()
FROM monitored_models
ORDER BY model_name, created_at DESC
ON CONFLICT (name) DO NOTHING;

ALTER TABLE monitored_models ADD COLUMN IF NOT EXISTS registered_model_id INTEGER;

UPDATE monitored_models m
SET registered_model_id = rm.id
FROM registered_models rm
WHERE rm.name = m.model_name AND m.registered_model_id IS NULL;

ALTER TABLE monitored_models ALTER COLUMN registered_model_id SET NOT NULL;

ALTER TABLE monitored_models
    DROP CONSTRAINT IF EXISTS monitored_models_registered_model_id_fkey;
ALTER TABLE monitored_models
    ADD CONSTRAINT monitored_models_registered_model_id_fkey
    FOREIGN KEY (registered_model_id) REFERENCES registered_models (id) ON DELETE RESTRICT;

ALTER TABLE monitored_models DROP COLUMN IF EXISTS model_name;
ALTER TABLE monitored_models DROP COLUMN IF EXISTS display_name;
ALTER TABLE monitored_models DROP COLUMN IF EXISTS input_price_per_million;
ALTER TABLE monitored_models DROP COLUMN IF EXISTS cached_input_price_per_million;
ALTER TABLE monitored_models DROP COLUMN IF EXISTS output_price_per_million;
ALTER TABLE monitored_models DROP COLUMN IF EXISTS currency;

COMMIT;
