"""
Prompt-Chill daemon — mindig fut, nappal dolgozik.
Feladata:
  1. Lekéri az extracted=FALSE seed-eket
  2. LLM extrakció (entity, fact, tags)
  3. Embedding generálás
  4. Redis frissítés
"""
import logging
import time

from axel_rem import db, redis_mem
from axel_rem.extract import extract_entity_fact
from axel_rem.embedder import embed

log = logging.getLogger(__name__)

POLL_INTERVAL = 30   # másodperc


class PromptChill:

    def run(self):
        log.info("[PROMPT-CHILL] Indul — poll: %ds", POLL_INTERVAL)
        while True:
            try:
                self._process_batch()
            except Exception as e:
                log.exception("[PROMPT-CHILL] Hiba: %s", e)
            time.sleep(POLL_INTERVAL)

    def _process_batch(self):
        seeds = db.seed_get_unprocessed(limit=20)
        if not seeds:
            return

        log.info("[PROMPT-CHILL] %d új seed feldolgozás", len(seeds))

        facts = [s["fact"] for s in seeds]
        chunks = [s.get("chunk", "") or "" for s in seeds]
        combined = [f + " " + c for f, c in zip(facts, chunks)]

        try:
            from axel_rem.embedder import embed_batch
            embeddings = embed_batch(combined)
        except Exception as e:
            log.warning("[PROMPT-CHILL] Embedding batch hiba: %s", e)
            embeddings = [None] * len(seeds)

        for seed, embedding in zip(seeds, embeddings):
            try:
                extracted = extract_entity_fact(
                    description=seed["fact"],
                    summary=seed.get("chunk", ""),
                )
                entity = extracted["entity"]
                fact   = extracted["fact"]
                tags   = extracted["tags"]

                db.seed_update(
                    mem_id=seed["id"],
                    entity=entity,
                    fact=fact,
                    tags=tags,
                    embedding=embedding,
                )

                if entity:
                    redis_mem.push_memory(
                        agent=seed["agent"],
                        mem_id=seed["id"],
                        entity=entity,
                        fact=fact,
                        strength=1.0,
                    )

                log.debug("[PROMPT-CHILL] #%d → entity=%s", seed["id"], entity or "(üres)")

            except Exception as e:
                log.warning("[PROMPT-CHILL] Seed #%d hiba: %s", seed["id"], e)
