-- Cognitive Reflex Decay support columns
-- Inspired by InsomnAI 3.0 by Frész Ferenc
-- https://github.com/ferencfresz/insomnai_3.0
--
-- dream_count: how many dream cycles have promoted this entity without it stabilising
-- is_canonical: TRUE once the entity exceeds the reflex-decay threshold — exempt from
--               decay and further consolidation

ALTER TABLE axel_rem_memory ADD COLUMN IF NOT EXISTS dream_count  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE axel_rem_memory ADD COLUMN IF NOT EXISTS is_canonical BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_rem_canonical ON axel_rem_memory (agent, entity) WHERE is_canonical = TRUE;
