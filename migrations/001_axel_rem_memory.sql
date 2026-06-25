-- axel_rem_memory: multi-agent semantic memory with pgvector
-- Dimension: 384 (all-MiniLM-L6-v2)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS axel_rem_memory (
    id          SERIAL PRIMARY KEY,
    entity      VARCHAR(200),
    fact        TEXT NOT NULL,
    chunk       TEXT,
    source_type VARCHAR(50),
    source_ref  TEXT,
    agent       VARCHAR(50) NOT NULL DEFAULT 'AXEL',
    strength    FLOAT NOT NULL DEFAULT 1.0,
    embedding   VECTOR(384),
    tags        TEXT[],
    extracted   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rem_entity      ON axel_rem_memory (entity);
CREATE INDEX IF NOT EXISTS idx_rem_agent       ON axel_rem_memory (agent);
CREATE INDEX IF NOT EXISTS idx_rem_created     ON axel_rem_memory (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rem_strength    ON axel_rem_memory (strength DESC);
CREATE INDEX IF NOT EXISTS idx_rem_unprocessed ON axel_rem_memory (id) WHERE extracted = FALSE;
