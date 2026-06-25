"""
Prompt-Chill daemon — mindig fut, nappal dolgozik.
Feladata:
  1. axel_rem_memory extracted=FALSE seed-ek feldolgozása
  2. axel_message rem_processed=FALSE üzenetek feldolgozása
  3. LLM extrakció (entity, fact, tags) + embedding + Redis frissítés
"""
import logging
import threading

from axel_rem import db, redis_mem
from axel_rem.extract import extract_entity_fact
from axel_rem.embedder import embed, embed_batch

log = logging.getLogger(__name__)

POLL_INTERVAL = 30   # másodperc — signal esetén azonnal felébred


class PromptChill:

    def run(self):
        log.info("[PROMPT-CHILL] Indul — poll: %ds", POLL_INTERVAL)
        try:
            from axel_rem.api import get_signal_event
            _event = get_signal_event()
        except Exception:
            _event = threading.Event()

        while True:
            try:
                self._process_seeds()
                self._process_messages()
            except Exception as e:
                log.exception("[PROMPT-CHILL] Hiba: %s", e)
            _event.wait(timeout=POLL_INTERVAL)
            _event.clear()

    # ── Seed feldolgozás (axel_rem_memory extracted=FALSE) ──────────────────

    def _process_seeds(self):
        seeds = db.seed_get_unprocessed(limit=20)
        if not seeds:
            return

        log.info("[PROMPT-CHILL] %d új seed feldolgozás", len(seeds))

        facts = [s["fact"] for s in seeds]
        chunks = [s.get("chunk", "") or "" for s in seeds]
        combined = [f + " " + c for f, c in zip(facts, chunks)]

        try:
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
                db.seed_update(
                    mem_id=seed["id"],
                    entity=extracted["entity"],
                    fact=extracted["fact"],
                    tags=extracted["tags"],
                    embedding=embedding,
                )
                if extracted["entity"]:
                    redis_mem.push_memory(
                        agent=seed["agent"],
                        mem_id=seed["id"],
                        entity=extracted["entity"],
                        fact=extracted["fact"],
                        strength=1.0,
                    )
                log.debug("[PROMPT-CHILL] seed #%d → %s", seed["id"], extracted["entity"] or "(üres)")
            except Exception as e:
                log.warning("[PROMPT-CHILL] Seed #%d hiba: %s", seed["id"], e)

    # ── Üzenet feldolgozás (axel_message rem_processed=FALSE) ───────────────

    def _process_messages(self):
        messages = db.messages_get_unprocessed(limit=15)
        if not messages:
            return

        log.info("[PROMPT-CHILL] %d új üzenet feldolgozás", len(messages))

        for msg in messages:
            try:
                title = msg.get("title") or ""
                body = msg.get("body") or ""
                text = f"{title} {body}".strip()[:600]

                extracted = extract_entity_fact(description=text, summary="")
                entity = extracted["entity"]
                fact = extracted["fact"]

                if entity and fact:
                    try:
                        embedding = embed(f"{entity} {fact}")
                    except Exception:
                        embedding = None

                    mem_id = db.memory_insert_extracted(
                        entity=entity,
                        fact=fact,
                        chunk=text[:800],
                        source_ref=f"msg://{msg['id']}",
                        agent="AXEL",
                        embedding=embedding,
                        tags=extracted["tags"],
                    )
                    redis_mem.push_memory(
                        agent="AXEL",
                        mem_id=mem_id,
                        entity=entity,
                        fact=fact,
                        strength=1.0,
                    )

                db.message_mark_processed(msg["id"])
                log.debug("[PROMPT-CHILL] msg #%d → %s", msg["id"], entity or "(üres)")

            except Exception as e:
                log.warning("[PROMPT-CHILL] Message #%d hiba: %s", msg["id"], e)
